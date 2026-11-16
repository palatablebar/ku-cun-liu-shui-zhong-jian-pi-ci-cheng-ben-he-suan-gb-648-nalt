"""导出：流水证据行、FIFO 层、单据成本。

每一行都带文件行号与原因码；编码、单据号、原值按字符串逐字保留，
数量与金额按四位小数输出，表头自带单位。
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from ... import config, reasons

MOVEMENT_HEADER = (
    "文件行号", "状态", "原因码", "原因", "单据号", "单据行号", "版本", "类型", "方向",
    "仓库", "物料编码", "数量原文", "单位原文", "数量kg", "单价原文", "单价", "发生时间UTC",
    "关联单号", "序号", "退货方式", "退货成本来源", "估算/恢复单价",
)

LAYER_HEADER = (
    "层号", "物料编码", "仓库", "来源", "单据号", "建层时间", "层成本", "入层数量",
    "剩余数量", "层金额", "临时层", "成本来源", "待回原单号",
)

COST_HEADER = (
    "单据号", "方向", "消耗数量kg", "消耗金额", "退货恢复", "净成本", "消耗段数",
)

RETURN_HEADER = (
    "文件行号", "退货单号", "行号", "仓库", "物料编码", "退货数量kg", "原单号",
    "入账方式", "状态", "原因码", "原因", "成本来源", "入账单价", "入账数量kg",
    "入账金额", "临时层号", "恢复层号", "库存与成本影响",
)

RETURN_MODE_EXPORT = {
    "RESTORED": "已回原层",
    "PROVISIONAL": "临时入账",
    "PARKED": "待原单未入账",
}

RETURN_COST_SOURCE_EXPORT = {
    "RETURN_PRICE": "退货自带单价",
    "LATEST_LAYER": "同物料同仓最新层成本",
    "FALLBACK": "系统兜底单价",
    "ORIGINAL_LAYER": "原批次层成本",
}


def _buffer() -> tuple[io.StringIO, "csv.writer"]:
    buffer = io.StringIO()
    return buffer, csv.writer(buffer, lineterminator="\n")


def _format(value, quantum) -> str:
    if value in (None, ""):
        return ""
    return format(config.dec(value).quantize(quantum), "f")


def movements_csv(rows: list[dict]) -> str:
    buffer, writer = _buffer()
    writer.writerow(MOVEMENT_HEADER)
    for row in rows:
        writer.writerow(
            [
                row.get("row_no", ""),
                row.get("status", ""),
                row.get("reason", ""),
                reasons.label(row.get("reason", "")) if row.get("reason") else "",
                row.get("doc_no", ""),
                row.get("line_no", 0),
                row.get("revision", 1),
                row.get("doc_type", ""),
                row.get("direction", ""),
                row.get("warehouse", ""),
                row.get("sku", ""),
                row.get("raw_qty", ""),
                row.get("raw_unit", ""),
                _format(row.get("qty_kg"), config.QTY_QUANTUM),
                row.get("raw_cost", ""),
                _format(row.get("price"), config.COST_QUANTUM),
                row.get("ts_utc", ""),
                row.get("ref_doc", ""),
                row.get("seq", 0),
                RETURN_MODE_EXPORT.get(row.get("return_mode", ""), ""),
                RETURN_COST_SOURCE_EXPORT.get(row.get("cost_source", ""), ""),
                _format(row.get("estimated_cost"), config.COST_QUANTUM)
                if row.get("estimated_cost") not in (None, "") else "",
            ]
        )
    return buffer.getvalue()


def layers_csv(layers: list[dict]) -> str:
    buffer, writer = _buffer()
    writer.writerow(LAYER_HEADER)
    for layer in layers:
        value = config.dec(layer["qty_remaining"]) * config.dec(layer["unit_cost"])
        writer.writerow(
            [
                layer["layer_id"],
                layer["sku"],
                layer["warehouse"],
                layer["origin"],
                layer.get("doc_no", ""),
                layer.get("created_ts", ""),
                _format(layer["unit_cost"], config.COST_QUANTUM),
                _format(layer["qty_in"], config.QTY_QUANTUM),
                _format(layer["qty_remaining"], config.QTY_QUANTUM),
                _format(value, config.VALUE_QUANTUM),
                "是" if layer.get("provisional") else "否",
                RETURN_COST_SOURCE_EXPORT.get(layer.get("cost_source", ""), ""),
                layer.get("ref_doc", ""),
            ]
        )
    return buffer.getvalue()


def returns_csv(ledger: list[dict]) -> str:
    """退货台账导出：与 /api/cost/returns 及页面退货面板同源同数。"""
    buffer, writer = _buffer()
    writer.writerow(RETURN_HEADER)
    for row in ledger:
        writer.writerow(
            [
                row.get("row_no", ""),
                row.get("doc_no", ""),
                row.get("line_no", 0),
                row.get("warehouse", ""),
                row.get("sku", ""),
                row.get("qty_kg", ""),
                row.get("ref_doc", ""),
                RETURN_MODE_EXPORT.get(row.get("return_mode", ""), row.get("mode_label", "")),
                row.get("status", ""),
                row.get("reason", ""),
                row.get("reason_label", ""),
                RETURN_COST_SOURCE_EXPORT.get(
                    row.get("cost_source", ""), row.get("cost_source_label", "")),
                row.get("unit_cost", ""),
                row.get("booked_qty_kg", ""),
                row.get("booked_value", ""),
                row.get("provisional_layer", ""),
                row.get("layer_ids", ""),
                row.get("impact", ""),
            ]
        )
    return buffer.getvalue()


def cost_csv(rows: list[dict]) -> str:
    buffer, writer = _buffer()
    writer.writerow(COST_HEADER)
    for row in rows:
        writer.writerow(
            [
                row["doc_no"],
                row["kinds"],
                row["qty"],
                row["consumed"],
                row["returned"],
                row["net"],
                row["segments"],
            ]
        )
    return buffer.getvalue()


def decimal_sum(values) -> Decimal:
    return sum((config.dec(value) for value in values), Decimal("0"))
