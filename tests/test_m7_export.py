"""M7 导出：证据行、层、成本 CSV 与带 sha256 的运行清单。"""
from __future__ import annotations

import json

from conftest import fixture_json

EXPECTED = fixture_json("expected_totals.json")


def test_movements_csv_keeps_raw_and_reason(client, demo):
    response = client.get("/api/export/movements.csv")
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert len(lines) == 35
    assert "原因码" in lines[0] and "数量原文" in lines[0]
    assert any("TRUNCATED_TAIL" in line for line in lines)
    assert any('"1,200"' in line or "1,200" in line for line in lines)


def test_leading_zero_sku_survives_export(client, demo):
    # TRAP-08：编码按字符串导出，前导零不丢；"123" 与 "000123" 是两个物料
    text = client.get("/api/export/movements.csv").text
    assert ",000123," in text
    lines = text.splitlines()
    leading = [line for line in lines if ",000123," in line]
    bare = [line for line in lines if ",123," in line]
    assert leading and bare
    assert all("SKU_UNKNOWN" in line for line in bare)


def test_layers_csv(client, demo):
    response = client.get("/api/export/layers.csv")
    lines = response.text.strip().splitlines()
    assert len(lines) == 15
    assert "层金额" in lines[0]
    assert "临时层" in lines[0]
    assert any(line.startswith("L000001") for line in lines)
    assert any("100.0000" in line for line in lines)
    provisional_lines = [line for line in lines if line.endswith(",是") or ",是," in line]
    assert len(provisional_lines) == 2


def test_returns_csv_matches_ledger(client, demo):
    text = client.get("/api/export/returns.csv").text
    lines = text.strip().splitlines()
    # 3 条退货 + 表头
    assert len(lines) == 4
    assert "入账方式" in lines[0] and "库存与成本影响" in lines[0]
    assert any("TH-2026" in line and "临时入账" in line for line in lines)
    assert any("TH-2009" in line and "已回原层" in line for line in lines)
    # 与 /api/cost/returns 同源同数
    payload = client.get("/api/cost/returns").json()
    assert payload["summary"]["total_rows"] == 3
    assert len(payload["rows"]) == 3
    assert {row["doc_no"] for row in payload["rows"]} == {
        "TH-2009", "TH-2010", "TH-2026"}


def test_movements_csv_carries_return_mode(client, demo):
    text = client.get("/api/export/movements.csv").text
    lines = text.strip().splitlines()
    assert "退货方式" in lines[0]
    th2026 = [line for line in lines if line.startswith(("32,",)) and "TH-2026" in line]
    assert th2026 and "临时入账" in th2026[0]


def test_cost_csv(client, demo):
    lines = client.get("/api/export/cost.csv").text.strip().splitlines()
    assert len(lines) == 6
    assert "净成本" in lines[0]
    assert sum(1 for line in lines if "CK-2002" in line) == 1


def test_manifest_has_run_id_and_input_sha(client, demo):
    payload = json.loads(client.get("/api/export/manifest.json").text)
    assert payload["run_id"] == demo["run_id"]
    assert len(payload["inputs"]["movements"]["sha256"]) == 64
    assert len(payload["inputs"]["opening"]["sha256"]) == 64
    assert len(payload["inputs"]["counts"]["sha256"]) == 64
    assert payload["inputs"]["movements"]["file"] == "movements_dirty.csv"
    assert payload["counts"]["applied"] == EXPECTED["counts"]["applied"]
    assert len(payload["checks"]) == 11
    assert payload["params"]["registry"]
    assert payload["params"]["return_policy"] == "provisional"
    assert payload["returns"]["provisional_rows"] == 2
    assert payload["returns"]["parked_returns"] == 0


def test_manifest_plain_decimal(client, demo):
    text = client.get("/api/export/manifest.json").text
    payload = json.loads(text)
    assert "E+" not in text and "e+" not in text
    assert payload["cost"]["cogs"].startswith(EXPECTED["cogs"][:6])
    assert payload["quantities"]["ending"] == EXPECTED["quantities"]["ending_qty"]


def test_unknown_export_kind(client, demo):
    response = client.get("/api/export/report.csv")
    assert response.status_code == 400
    assert response.json()["error"] == "E_EXPORT_KIND"


def test_export_without_run(client):
    response = client.get("/api/export/movements.csv")
    assert response.status_code == 409
    assert response.json()["error"] == "E_NO_RUN"
