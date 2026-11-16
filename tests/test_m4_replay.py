"""M4 时序重放：全序、超库存拒绝后继续、调拨双边、FIFO 消耗、退货回原层。"""
from __future__ import annotations

from decimal import Decimal

from conftest import (build_rows, fixture_json, opening_rows, parse_sample,
                      read_fixture, replay_sample)
from app.modules.m1_ingest import service as m1_service
from app.modules.m2_doc_rules import contract
from app.modules.m3_quality import service as m3_service
from app.modules.m4_replay import replay as replay_mod

EXPECTED = fixture_json("expected_totals.json")
EXPECTED_LAYERS = fixture_json("expected_layers.json")


def layer(outcome: dict, layer_id: str) -> dict:
    return [item for item in outcome["layers"] if item["layer_id"] == layer_id][0]


def rows_by_doc(outcome_rows, doc_no: str):
    return [row for row in outcome_rows if row["doc_no"] == doc_no]


def run_fixture(settings, name: str) -> tuple[dict, list[dict]]:
    parsed = m1_service.read_file(
        settings, name, read_fixture(name), contract.aliases("movements"))
    built = m3_service.build(settings, parsed)
    outcome = replay_mod.replay(built["rows"], opening_rows(settings), settings)
    return outcome, built["rows"]


def test_replay_totals_match_expected(settings):
    parsed = parse_sample(settings)
    built = build_rows(settings, parsed)
    outcome = replay_mod.replay(built["rows"], opening_rows(settings), settings)
    totals = outcome["totals"]
    assert format(totals["ending_qty"], "f") == EXPECTED["quantities"]["ending_qty"]
    assert format(totals["cogs"], "f").startswith(EXPECTED["cogs"][:6])
    assert len(outcome["layers"]) == EXPECTED["layers"]
    assert format(outcome["layers"][0]["qty_remaining"], "f") == "100.0000"

def test_replay_totals_survive_input_permutation(settings):
    # TRAP-13：文件乱序不影响重放，全序按发生时间排
    baseline = replay_sample(settings)
    indices = list(range(34))
    shuffled = list(reversed(indices))
    other = replay_sample(settings, shuffle=shuffled)
    for key in ("opening_qty", "in_qty", "out_qty", "adj_in_qty", "adj_out_qty",
                "ending_qty", "cogs", "ending_value"):
        assert baseline["totals"][key] == other["totals"][key]
    under_key = {(item["sku"], item["warehouse"]): item["qty_remaining"]
                 for item in baseline["layers"]}
    same_key = {(item["sku"], item["warehouse"]): item["qty_remaining"]
                for item in other["layers"]}
    assert under_key == same_key


def test_out_exceeds_stock_rejected_then_replay_continues(settings):
    outcome = replay_sample(settings)
    rows = outcome["rows"]
    rejected = [row for row in rows if row["reason"] == "OUT_EXCEEDS_STOCK"]
    assert {row["row_no"] for row in rejected} == {7, 35}
    assert all(row["status"] == "quarantine" for row in rejected)
    later = [row for row in rows if row["doc_no"] == "RK-2019" and row["status"] == "valid"]
    assert later and later[0]["applied_seq"] > 0
    assert outcome["totals"]["ending_qty"] > 0


def test_transfer_pair_creates_layer_in_target(settings):
    # TRAP-21：调拨必须双边，成对时出边消耗、入边按消耗成本建层
    outcome = replay_sample(settings)
    target = layer(outcome, "L000008")
    assert target["warehouse"] == "WH-C"
    assert target["sku"] == "000456"
    assert target["unit_cost"] == Decimal("8.0000")
    assert target["qty_in"] == Decimal("30.0000")
    assert outcome["pairs"] == 1


def test_transfer_missing_leg_quarantined(settings):
    # TRAP-21：只有一条边的调拨整组隔离
    outcome = replay_sample(settings)
    broken = [row for row in outcome["rows"] if row["doc_no"] == "DB-2006"]
    assert broken and broken[0]["reason"] == "TRN_MISSING_LEG"
    assert broken[0]["status"] == "quarantine"
    fixture_outcome, fixture_rows = run_fixture(settings, "transfer_single_leg.csv")
    assert fixture_rows[0]["reason"] == "TRN_MISSING_LEG"
    assert fixture_outcome["totals"]["trn_in_qty"] == 0


def test_transfer_quantity_mismatch_quarantined(settings):
    outcome = replay_sample(settings)
    legs = [row for row in outcome["rows"] if row["doc_no"] == "DB-2027"]
    assert len(legs) == 2
    assert {row["reason"] for row in legs} == {"TRN_QTY_MISMATCH"}
    assert {row["status"] for row in legs} == {"quarantine"}


def test_fifo_consumes_earliest_layers(settings):
    outcome = replay_sample(settings)
    segments = [seg for seg in outcome["segments"] if seg["out_doc"] == "CK-2002"]
    assert [seg["layer_id"] for seg in segments] == ["L000001", "L000005"]
    assert [seg["qty"] for seg in segments] == [Decimal("100"), Decimal("800")]
    assert sum((seg["amount"] for seg in segments), Decimal("0")) == Decimal("9000")


def test_return_with_original_restores_layer(settings):
    outcome = replay_sample(settings)
    restored = [seg for seg in outcome["segments"] if seg["kind"] == "RET"]
    assert len(restored) == 1
    assert restored[0]["layer_id"] == "L000001"
    assert restored[0]["qty"] == Decimal("100")
    assert layer(outcome, "L000001")["qty_remaining"] == Decimal("100.0000")
    assert restored[0]["amount"] == Decimal("1000")
    # TH-2009 回原层 1000，TH-2010/TH-2026 临时层按各自单价 95 + 100
    assert outcome["totals"]["return_value"] == Decimal("1195")
    restored_rows = [row for row in outcome["rows"] if row.get("return_mode") == "RESTORED"]
    assert [row["doc_no"] for row in restored_rows] == ["TH-2009"]


def test_return_without_original_creates_new_layer(settings):
    outcome = replay_sample(settings)
    # TH-2010 无原单：provisional 策略下建临时层，带临时标记与成本来源
    new_layer = layer(outcome, "L000009")
    assert new_layer["origin"] == "退货入库"
    assert new_layer["unit_cost"] == Decimal("9.5000")
    assert new_layer["qty_in"] == Decimal("10.0000")
    assert new_layer["provisional"] is True
    assert new_layer["cost_source"] == "RETURN_PRICE"
    th2010 = [row for row in outcome["rows"] if row["doc_no"] == "TH-2010"][0]
    assert th2010["return_mode"] == "PROVISIONAL"
    assert th2010["status"] == "valid"


def test_return_without_original_wait_policy_parks(settings_wait):
    # wait_original：无原单的退货不建层、不入在库，整行隔离
    from pathlib import Path

    from app.modules.m1_ingest import service as m1_service
    from app.modules.m2_doc_rules import contract
    from app.modules.m3_quality import service as m3_service

    raw = Path(settings_wait.sample_dir, "movements_dirty.csv").read_bytes()
    parsed = m1_service.read_file(
        settings_wait, "movements_dirty.csv", raw,
        contract.aliases("movements"))
    built = m3_service.build(settings_wait, parsed)
    outcome = replay_mod.replay(built["rows"], opening_rows(settings_wait), settings_wait)
    assert len(outcome["layers"]) == 12
    parked = {row["doc_no"]: row for row in outcome["rows"]
              if row.get("reason") == "RETURN_DOC_NOT_FOUND"}
    assert set(parked) == {"TH-2010", "TH-2026"}
    assert outcome["totals"]["parked_returns"] == 2
    assert outcome["totals"]["provisional_rows"] == 0


def test_return_edge_cases(settings):
    outcome, rows = run_fixture(settings, "return_cases.csv")
    status = {row["doc_no"]: (row["status"], row["reason"]) for row in rows}
    assert status["TH-5403"] == ("valid", "")
    assert status["TH-5404"] == ("quarantine", "RETURN_EXCEEDS_ORIGINAL")
    # TH-5405 原单不存在：provisional 策略按自带单价建临时层入在库
    assert status["TH-5405"] == ("valid", "")
    provisional = [item for item in outcome["layers"] if item.get("provisional")]
    assert len(provisional) == 1
    assert provisional[0]["qty_remaining"] == Decimal("5.0000")
    assert provisional[0]["ref_doc"] == "XX-0000"
    th5405 = [row for row in rows if row["doc_no"] == "TH-5405"][0]
    assert th5405["return_mode"] == "PROVISIONAL"
    assert layer(outcome, "L000001")["qty_remaining"] == Decimal("80.0000")


def test_return_edge_cases_wait_policy(settings_wait):
    outcome, rows = run_fixture(settings_wait, "return_cases.csv")
    status = {row["doc_no"]: (row["status"], row["reason"]) for row in rows}
    assert status["TH-5403"] == ("valid", "")
    assert status["TH-5404"] == ("quarantine", "RETURN_EXCEEDS_ORIGINAL")
    assert status["TH-5405"] == ("quarantine", "RETURN_DOC_NOT_FOUND")
    assert layer(outcome, "L000001")["qty_remaining"] == Decimal("80.0000")
    assert all(not item.get("provisional") for item in outcome["layers"])


def test_provisional_without_any_cost_basis_is_quarantined(settings):
    # 无自带单价、同物料同仓也没有可参考层、兜底单价为 0：无法估算，隔离而非建零成本层
    from app import config as config_mod

    original = config_mod.PROVISIONAL_FALLBACK_COST
    config_mod.PROVISIONAL_FALLBACK_COST = Decimal("0")
    try:
        outcome, rows = run_fixture(settings, "return_no_cost.csv")
    finally:
        config_mod.PROVISIONAL_FALLBACK_COST = original
    only = rows[0]
    assert only["status"] == "quarantine"
    assert only["reason"] == "RETURN_NO_COST_BASIS"
    assert all(not item.get("provisional") for item in outcome["layers"])
    assert outcome["totals"]["provisional_rows"] == 0


def test_provisional_cost_falls_back_to_latest_layer(settings):
    # 退货没填单价时，取同物料同仓最新非临时层成本，而不是零成本
    text = (
        "单据号,行号,类型,仓库,物料编码,数量,单位,单价,发生时间,方向,关联单号,版本\n"
        "RK-8201,1,采购入库,WH-B,000123,20,kg,12.5000,2026-08-02T09:00:00+08:00,入,,1\n"
        "TH-8202,1,退货入库,WH-B,000123,4,kg,,2026-08-02T10:00:00+08:00,入,CK-8888,1\n"
    )
    parsed = m1_service.read_file(
        settings, "inline.csv", text.encode("utf-8"),
        contract.aliases("movements"))
    built = m3_service.build(settings, parsed)
    outcome = replay_mod.replay(built["rows"], [], settings)
    th = [row for row in outcome["rows"] if row["doc_no"] == "TH-8202"][0]
    assert th["return_mode"] == "PROVISIONAL"
    assert th["cost_source"] == "LATEST_LAYER"
    assert th["estimated_cost"] == Decimal("12.5000")
    provisional = [item for item in outcome["layers"] if item.get("provisional")]
    assert len(provisional) == 1
    assert provisional[0]["unit_cost"] == Decimal("12.5000")
