"""退货台账：逐行说明退货是临时入账还是等原单回原层，以及随后变化的库存与成本。

三种入账方式（return_mode）：
- RESTORED：原单已到，按原消耗顺序回原批次层，不建新层，冲减原单出库成本；
- PROVISIONAL：原单晚到，按参考成本建临时层当天入在库，原单补到重放后整层消失并回原层；
- PARKED：wait_original 策略下原单缺失，整行隔离不入在库，等原单补到再处理。

页面、API 与 CSV 导出共用本模块，保证工作台与导出结果一致。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config, reasons

# 成本来源码 -> 中文说明。
COST_SOURCE_LABELS = {
    "RETURN_PRICE": "退货自带单价",
    "LATEST_LAYER": "同物料同仓最新层成本",
    "FALLBACK": "系统兜底单价",
    "ORIGINAL_LAYER": "原批次层成本",
}

MODE_LABELS = {
    "RESTORED": "已回原层",
    "PROVISIONAL": "临时入账",
    "PARKED": "待原单未入账",
}


def mode_of(row: dict) -> str:
    """逐行退货状态；隔离且没有显式模式的统一归为待原单。"""
    if row.get("return_mode") in MODE_LABELS:
        return row["return_mode"]
    if row.get("status") == "quarantine":
        return "PARKED"
    return row.get("return_mode") or ""


def build_ledger(movement_rows: list[dict], layers: list[dict],
                 segments: list[dict]) -> list[dict]:
    """装配退货台账行；数量与金额全部格式化为四位小数字符串，供页面与导出同源使用。"""
    layer_index = {layer["layer_id"]: layer for layer in layers}
    restored_qty: dict[str, Decimal] = {}
    restored_value: dict[str, Decimal] = {}
    restored_layers: dict[str, list[str]] = {}
    for seg in segments:
        if seg.get("kind") != "RET":
            continue
        doc = seg.get("out_doc") or ""
        restored_qty[doc] = restored_qty.get(doc, Decimal("0")) + config.dec(seg["qty"])
        restored_value[doc] = restored_value.get(doc, Decimal("0")) + config.dec(seg["amount"])
        restored_layers.setdefault(doc, []).append(seg["layer_id"])

    provisional_layers = [
        layer for layer in layers if layer.get("provisional")
    ]
    provisional_by_doc = {layer.get("doc_no"): layer for layer in provisional_layers}

    ledger: list[dict] = []
    for row in movement_rows:
        if row.get("doc_type") != "退货入库":
            continue
        doc = row.get("doc_no") or ""
        mode = mode_of(row)
        qty = config.dec(row.get("qty_kg"))
        ref_doc = row.get("ref_doc") or ""
        provisional = provisional_by_doc.get(doc)
        layer_ids = restored_layers.get(doc, [])

        if mode == "RESTORED":
            booked_qty = restored_qty.get(doc, qty)
            booked_value = restored_value.get(doc, Decimal("0"))
            unit_cost = (booked_value / booked_qty).quantize(config.COST_QUANTUM) \
                if booked_qty else Decimal("0")
            cost_basis = "ORIGINAL_LAYER"
            impact = (
                f"原单 {ref_doc or '—'} 已到：数量放回原层"
                f"（{','.join(layer_ids) or '—'}），不建新层，冲减原单出库成本 "
                f"{format(config.quantize_value(booked_value), 'f')} 元"
            )
        elif mode == "PROVISIONAL":
            booked_qty = qty
            unit_cost = config.dec(
                row.get("estimated_cost")
                or (provisional or {}).get("unit_cost") or 0)
            booked_value = qty * unit_cost
            cost_basis = row.get("cost_source") or (provisional or {}).get(
                "cost_source") or ""
            impact = (
                f"原单 {ref_doc or '（未填）'} 未到：先建临时层当天入在库，"
                f"原单补到重放后临时层消失、按原批次成本回原层，"
                f"届时在库数量不变、层成本与出库成本调整"
            )
        else:
            booked_qty = Decimal("0")
            unit_cost = Decimal("0")
            booked_value = Decimal("0")
            cost_basis = ""
            impact = (
                f"原单 {ref_doc or '（未填）'} 未到：整行隔离不入在库，"
                f"等原单补到后重放再回原层"
            )

        ledger.append(
            {
                "row_no": row.get("row_no", 0),
                "doc_no": doc,
                "line_no": row.get("line_no", 0),
                "warehouse": row.get("warehouse", ""),
                "sku": row.get("sku", ""),
                "qty_kg": format(config.quantize_qty(qty), "f"),
                "ref_doc": ref_doc,
                "status": row.get("status", ""),
                "reason": row.get("reason", ""),
                "reason_label": reasons.label(row.get("reason", "")) if row.get("reason") else "",
                "return_mode": mode,
                "mode_label": MODE_LABELS.get(mode, mode),
                "cost_source": cost_basis,
                "cost_source_label": COST_SOURCE_LABELS.get(cost_basis, cost_basis),
                "unit_cost": format(config.quantize_cost(unit_cost), "f"),
                "booked_qty_kg": format(config.quantize_qty(booked_qty), "f"),
                "booked_value": format(config.quantize_value(booked_value), "f"),
                "layer_ids": ",".join(layer_ids),
                "provisional_layer": (provisional or {}).get("layer_id", ""),
                "impact": impact,
            }
        )
    return ledger


def summarize(ledger: list[dict]) -> dict:
    def sum_qty(key: str) -> Decimal:
        return sum((config.dec(row[key]) for row in ledger), Decimal("0"))

    provisional = [row for row in ledger if row["return_mode"] == "PROVISIONAL"]
    restored = [row for row in ledger if row["return_mode"] == "RESTORED"]
    parked = [row for row in ledger if row["return_mode"] == "PARKED"]
    return {
        "total_rows": len(ledger),
        "provisional_rows": len(provisional),
        "restored_rows": len(restored),
        "parked_rows": len(parked),
        "provisional_qty": format(config.quantize_qty(
            sum((config.dec(row["qty_kg"]) for row in provisional), Decimal("0"))), "f"),
        "provisional_value": format(config.quantize_value(
            sum((config.dec(row["booked_value"]) for row in provisional), Decimal("0"))), "f"),
        "restored_value": format(config.quantize_value(
            sum((config.dec(row["booked_value"]) for row in restored), Decimal("0"))), "f"),
        "booked_qty": format(config.quantize_qty(sum_qty("booked_qty_kg")), "f"),
    }
