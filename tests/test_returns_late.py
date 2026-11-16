"""退货原单晚到两日场景：当天临时入在库 → 原单补到重放回原层，库存不重复、成本归位。"""
from __future__ import annotations

from decimal import Decimal


def import_sample(client, name: str) -> dict:
    response = client.post("/api/movements/import", data={"sample": name})
    assert response.status_code == 200, response.text
    return response.json()


def test_day1_provisional_then_day2_restore(client):
    day1 = import_sample(client, "movements_returns_late_day1.csv")
    # 当天：3 行全部采纳，TH-7103 原单晚到先临时入在库
    assert day1["counts"]["quarantined"] == 0
    assert day1["returns"]["provisional_rows"] == 1
    assert Decimal(day1["returns"]["provisional_qty"]) == Decimal("5")
    assert Decimal(day1["returns"]["provisional_value"]) == Decimal("55")

    cost1 = client.get("/api/cost").json()
    ledger1 = {row["doc_no"]: row for row in cost1["returns"]}
    assert ledger1["TH-7103"]["return_mode"] == "PROVISIONAL"
    assert ledger1["TH-7103"]["cost_source"] == "RETURN_PRICE"
    assert Decimal(ledger1["TH-7103"]["unit_cost"]) == Decimal("11")
    assert len(cost1["provisional_layers"]) == 1
    ending1 = Decimal(day1["quantities"]["ending_qty"])
    cogs1 = Decimal(day1["cogs"])

    # 原单补到后重跑：临时层消失、回原批次层
    day2 = import_sample(client, "movements_returns_late_day2.csv")
    cost2 = client.get("/api/cost").json()
    ledger2 = {row["doc_no"]: row for row in cost2["returns"]}
    assert ledger2["TH-7103"]["return_mode"] == "RESTORED"
    assert ledger2["TH-7103"]["layer_ids"]
    assert cost2["provisional_layers"] == []

    # 在库数量不变（不重复增加库存），层数减少一层；临时层不影响出库成本，
    # 回原层后按原批次单价 10 冲减出库成本 50（220 → 170）
    assert Decimal(day2["quantities"]["ending_qty"]) == ending1
    assert day2["layers"] == day1["layers"] - 1
    assert Decimal(day2["cogs"]) == cogs1 - Decimal("50")
    assert day2["returns"]["restored_returns"] == 2
    assert day2["returns"]["provisional_rows"] == 0

    # 导出与工作台同源
    csv_text = client.get("/api/export/returns.csv").text
    assert "TH-7103" in csv_text and "已回原层" in csv_text
    api_rows = client.get("/api/cost/returns").json()["rows"]
    assert len(csv_text.strip().splitlines()) == len(api_rows) + 1


def test_day1_wait_original_policy_keeps_stock_out(client_wait):
    day1 = import_sample(client_wait, "movements_returns_late_day1.csv")
    # wait_original：原单晚到的退货不入在库，隔离等待
    assert day1["returns"]["parked_returns"] == 1
    assert day1["returns"]["provisional_rows"] == 0
    cost = client_wait.get("/api/cost").json()
    assert cost["provisional_layers"] == []
    parked = {row["doc_no"]: row for row in cost["returns"]
              if row["return_mode"] == "PARKED"}
    assert set(parked) == {"TH-7103"}
    # 比 provisional 口径少 5kg 在库
    assert Decimal(day1["quantities"]["ending_qty"]) == Decimal("160.5000")
    quarantine = client_wait.get("/api/quarantine").json()
    reasons = {row["reason"] for row in quarantine["rows"]}
    assert "RETURN_DOC_NOT_FOUND" in reasons
    csv_text = client_wait.get("/api/quarantine.csv").text
    assert "TH-7103" in csv_text and "RETURN_DOC_NOT_FOUND" in csv_text
