"""M5 FIFO 成本：十进制合计、按单据归集、层金额恒等、调拨层成本。"""
from __future__ import annotations

from decimal import Decimal

from conftest import fixture_json


def cost(client) -> dict:
    return client.get("/api/cost").json()


def test_cogs_is_exact_decimal(client, demo):
    # TRAP-10：float 累加会差一分，这里用 Decimal 逐段相加
    payload = cost(client)
    total = Decimal(payload["cogs_totals"]["net"])
    summed = sum((Decimal(row["net"]) for row in payload["cogs"]), Decimal("0"))
    assert total == summed == Decimal("8520")
    assert 0.1 + 0.2 != 0.3


def test_cogs_by_doc_covers_consumption_and_return(client, demo):
    payload = cost(client)
    docs = {row["doc_no"]: row for row in payload["cogs"]}
    assert set(docs) == {"CK-2002", "CK-2014", "DB-2008", "PD-2013", "TH-2009"}
    assert Decimal(docs["CK-2002"]["net"]) == Decimal("9000")
    assert Decimal(docs["TH-2009"]["returned"]) == Decimal("1000")
    assert Decimal(docs["TH-2009"]["net"]) == Decimal("-1000")


def test_layer_value_matches_run_totals(client, demo):
    payload = cost(client)
    expected = fixture_json("expected_totals.json")
    assert Decimal(payload["layer_totals"]["value"]) == Decimal(expected["ending_value"])
    assert Decimal(payload["layer_totals"]["qty"]) == Decimal(
        expected["quantities"]["ending_qty"])
    assert payload["layer_totals"]["layers"] == 14
    assert all(item["ok"] for item in payload["checks"])


def test_provisional_layers_are_flagged_and_valued(client, demo):
    # 原单晚到的退货建临时层：带 provisional 标记、成本来源与待回原单号
    payload = cost(client)
    provisional = {layer["doc_no"]: layer for layer in payload["provisional_layers"]}
    assert set(provisional) == {"TH-2010", "TH-2026"}
    th2026 = provisional["TH-2026"]
    assert Decimal(th2026["unit_cost"]) == Decimal("10.0000")
    assert th2026["provisional"] == 1
    assert th2026["cost_source"] == "RETURN_PRICE"
    assert th2026["ref_doc"] == "XX-9999"
    normal = [layer for layer in payload["layers"] if not layer.get("provisional")]
    assert all(layer.get("provisional") in (0, None) for layer in normal)


def test_returns_ledger_modes(client, demo):
    payload = cost(client)
    ledger = {row["doc_no"]: row for row in payload["returns"]}
    assert set(ledger) == {"TH-2009", "TH-2010", "TH-2026"}
    assert ledger["TH-2009"]["return_mode"] == "RESTORED"
    assert ledger["TH-2009"]["layer_ids"] == "L000001"
    assert Decimal(ledger["TH-2009"]["booked_value"]) == Decimal("1000")
    assert ledger["TH-2010"]["return_mode"] == "PROVISIONAL"
    assert ledger["TH-2026"]["return_mode"] == "PROVISIONAL"
    assert Decimal(ledger["TH-2026"]["booked_value"]) == Decimal("100")
    assert ledger["TH-2026"]["provisional_layer"] == "L000014"
    summary = payload["return_summary"]
    assert summary["provisional_rows"] == 2
    assert summary["restored_rows"] == 1
    assert summary["parked_rows"] == 0
    assert Decimal(summary["provisional_value"]) == Decimal("195")


def test_transfer_layer_unit_cost_from_consumed_layers(client, demo):
    payload = cost(client)
    layers = {layer["layer_id"]: layer for layer in payload["layers"]}
    assert Decimal(layers["L000008"]["unit_cost"]) == Decimal("8.0000")
    assert layers["L000008"]["origin"] == "调拨"
    assert Decimal(layers["L000008"]["qty_in"]) == Decimal("30")


def test_consumption_kinds(client, demo):
    payload = cost(client)
    kinds = {row["kind"]: row for row in payload["kinds"]}
    assert set(kinds) == {"OUT", "TRN", "ADJ", "RET"}
    assert Decimal(kinds["OUT"]["qty"]) == Decimal("910")
    assert Decimal(kinds["OUT"]["amount"]) == Decimal("9240")
    assert Decimal(kinds["TRN"]["amount"]) == Decimal("240")
    assert Decimal(kinds["ADJ"]["amount"]) == Decimal("40")
    assert Decimal(kinds["RET"]["amount"]) == Decimal("1000")


def test_layer_rows_grouped_by_sku_and_warehouse(client, demo):
    payload = cost(client)
    groups = {(row["sku"], row["warehouse"]): row for row in payload["layer_rows"]}
    assert len(groups) == 6
    assert Decimal(groups[("000123", "WH-A")]["qty"]) == Decimal("521.2")
    assert Decimal(groups[("000123", "WH-A")]["value"]) == Decimal("5214.4")
    assert Decimal(groups[("000999", "WH-C")]["qty"]) == Decimal("2.5")


def test_cost_check_rows(client, demo):
    payload = cost(client)
    codes = [item["code"] for item in payload["checks"]]
    assert codes == ["layer_value", "layer_qty", "cogs"]
    assert all(item["ok"] for item in payload["checks"])


def test_cost_csv_matches_api(client, demo):
    payload = cost(client)
    response = client.get("/api/export/cost.csv")
    lines = response.text.strip().splitlines()
    assert len(lines) == len(payload["cogs"]) + 1
    assert "净成本" in lines[0]
    assert any("CK-2002" in line for line in lines)


def test_wait_original_policy_parks_returns(client_wait, demo_wait):
    # wait_original：原单缺失的退货整行隔离，不入在库、不建层
    payload = client_wait.get("/api/cost").json()
    ledger = {row["doc_no"]: row for row in payload["returns"]}
    assert ledger["TH-2010"]["return_mode"] == "PARKED"
    assert ledger["TH-2026"]["return_mode"] == "PARKED"
    assert payload["return_summary"]["parked_rows"] == 2
    assert payload["provisional_layers"] == []
    assert Decimal(payload["layer_totals"]["qty"]) == Decimal("900.2")
    assert Decimal(payload["layer_totals"]["value"]) == Decimal("8312.4")
    assert payload["layer_totals"]["layers"] == 12
