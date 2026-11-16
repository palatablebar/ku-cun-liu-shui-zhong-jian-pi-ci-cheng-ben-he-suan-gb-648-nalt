"""端到端：样例 → 采信 → 质量 → 重放 → 成本 → 盘点 → 导出，含幂等与失败输入。"""
from __future__ import annotations

from decimal import Decimal

from conftest import fixture_json

EXPECTED = fixture_json("expected_totals.json")
PAGES = ("/", "/movements", "/layers", "/cost", "/reconcile", "/export")


def test_all_pages_and_health(client, demo):
    for page in PAGES:
        response = client.get(page)
        assert response.status_code == 200, page
        assert "stockflow" in response.text.lower() or page in response.text
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["run_id"] == demo["run_id"]
    assert health["status_of_run"] == "ready"


def test_main_flow_numbers(client, demo):
    assert demo["counts"] == EXPECTED["counts"]
    assert demo["quantities"] == EXPECTED["quantities"]
    assert Decimal(demo["cogs"]) == Decimal(EXPECTED["cogs"])
    assert Decimal(demo["ending_value"]) == Decimal(EXPECTED["ending_value"])
    assert demo["layers"] == EXPECTED["layers"]
    assert demo["segments"] == EXPECTED["segments"]
    assert demo["reconcile_ok"] is True
    quarantine = client.get("/api/quarantine").json()
    assert quarantine["count"] == EXPECTED["counts"]["quarantined"]


def test_rerun_same_run_id_and_same_rows(client, demo):
    store = client.app.state.store
    before_rows = len(store.movements(demo["run_id"]))
    layers_before = len(store.layers(demo["run_id"]))
    again = client.post("/api/demo/run").json()
    assert again["run_id"] == demo["run_id"]
    assert again["quantities"] == demo["quantities"]
    assert again["cogs"] == demo["cogs"]
    assert again["counts"] == demo["counts"]
    assert len(store.movements(demo["run_id"])) == before_rows
    assert len(store.layers(demo["run_id"])) == layers_before


def test_provisional_then_original_arrives_no_double_stock(client, demo):
    # 原单晚到：先临时入账；补原单后整批重放，临时层消失回原层，库存与成本层不重复增加
    store = client.app.state.store
    first = client.get("/api/cost").json()
    provisional_docs = {layer["doc_no"] for layer in first["provisional_layers"]}
    assert "TH-2026" in provisional_docs
    ending_first = Decimal(first["layer_totals"]["qty"])

    # 用补了原单号的流水重跑（模拟原单补到）：TH-2026 指向一张真实已消耗出库单
    from pathlib import Path
    raw = Path(client.app.state.settings.sample_dir,
               "movements_dirty.csv").read_text(encoding="utf-8")
    patched_text = raw.replace(
        "TH-2026,1,退货入库,WH-A,000123,10,kg,10.0000,2026-03-10T11:00:00+08:00,入,XX-9999,1",
        "TH-2026,1,退货入库,WH-A,000123,10,kg,10.0000,2026-03-10T11:00:00+08:00,入,CK-2002,1",
    )
    assert patched_text != raw
    response = client.post(
        "/api/movements/import",
        files={"file": ("movements_dirty.csv", patched_text.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200, response.text
    second = response.json()
    cost = client.get("/api/cost").json()
    # 同数量仍在库（不重复增加）；TH-2026 的临时层消失、转为回原层（TH-2010 仍临时）
    assert Decimal(cost["layer_totals"]["qty"]) == ending_first
    provisional_docs = {layer["doc_no"] for layer in cost["provisional_layers"]}
    assert provisional_docs == {"TH-2010"}
    ledger = {row["doc_no"]: row for row in cost["returns"]}
    assert ledger["TH-2026"]["return_mode"] == "RESTORED"
    assert ledger["TH-2026"]["layer_ids"]
    # 出库成本多冲减一笔原批次成本
    assert Decimal(second["cogs"]) < Decimal(demo["cogs"])


def test_policy_switch_creates_distinct_run_and_keeps_previous(client, demo):
    store = client.app.state.store
    previous_id = demo["run_id"]
    switched = client.post("/api/replay?policy=wait_original").json()
    assert switched["run_id"] != previous_id
    # 原批次完整保留
    assert store.get_run(previous_id) is not None
    latest = client.get("/api/health").json()
    assert latest["run_id"] == switched["run_id"]
    cost = client.get("/api/cost").json()
    assert cost["return_policy"] == "wait_original"
    assert cost["return_summary"]["parked_rows"] == 2
    # 非法策略被拒
    bad = client.post("/api/replay?policy=bogus")
    assert bad.status_code == 400
    assert bad.json()["error"] == "E_RETURN_POLICY"


def test_clean_sample_uses_same_path(client):
    response = client.post("/api/movements/import",
                           data={"sample": "movements_clean.csv"})
    assert response.status_code == 200, response.text
    payload = response.json()
    # 期初四个层都在，清样例只走同一条重放路径
    assert Decimal(payload["quantities"]["ending_qty"]) == Decimal("252.5")
    assert Decimal(payload["cogs"]) == Decimal("600")
    health = client.get("/api/health").json()
    assert health["status_of_run"] == "ready"


def test_truncated_sample_isolated(client):
    response = client.post("/api/movements/import",
                           data={"sample": "movements_truncated.csv"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["quarantined"] == 1
    assert payload["counts"]["applied"] == 0
    assert payload["status"] == "empty"
    quarantine = client.get("/api/quarantine").json()
    assert quarantine["rows"][0]["reason"] == "TRUNCATED_TAIL"
    assert client.get("/api/health").json()["status_of_run"] == "empty"


def test_failure_paths(client, demo):
    missing = client.post("/api/movements/import", data={"sample": "no_such.csv"})
    assert missing.status_code == 404
    bad_body = client.post("/api/reconcile/counts", json={"count_no": "", "rows": []})
    assert bad_body.status_code == 400
    stale = client.post("/api/reconcile/counts", json={
        "count_no": "PD-E-1", "revision": 1,
        "rows": [{"sku": "000123", "warehouse": "WH-A", "counted_qty": "10"}]})
    assert stale.status_code == 200
    older = client.post("/api/reconcile/counts", json={
        "count_no": "PD-E-1", "revision": 0,
        "rows": [{"sku": "000123", "warehouse": "WH-A", "counted_qty": "10"}]})
    assert older.status_code == 400
