"""FIFO 层操作：建层、算在手、按最早层消耗、回原层恢复。

所有数量与单价是 Decimal；层金额保存精确乘积，对外展示时再量化到四位。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config


def key_of(sku: str, warehouse: str) -> tuple[str, str]:
    return sku, warehouse


def new_layer(layers: list[dict], sku: str, warehouse: str, origin: str, doc_no: str,
              line_no: int, ts: str, unit_cost: Decimal, qty: Decimal,
              provisional: bool = False, cost_source: str = "",
              ref_doc: str = "") -> dict:
    layer = {
        "layer_id": f"L{len(layers) + 1:06d}",
        "sku": sku,
        "warehouse": warehouse,
        "origin": origin,
        "doc_no": doc_no,
        "line_no": line_no,
        "created_ts": ts,
        "unit_cost": config.quantize_cost(unit_cost),
        "qty_in": config.quantize_qty(qty),
        "qty_remaining": config.quantize_qty(qty),
        # 临时层：退货原单晚到时按估算成本先入在库，原单补到重放后整层消失（回原层）。
        "provisional": provisional,
        "cost_source": cost_source,
        "ref_doc": ref_doc,
    }
    layers.append(layer)
    return layer


def latest_layer_cost(bucket: list[dict]) -> Decimal | None:
    """同物料同仓最新一个还有剩余的层成本，作为退货临时层的估算口径。

    临时层自身不作为后续临时层的估算来源，避免估算成本在退货之间自我复制。
    """
    for layer in reversed(bucket):
        if layer.get("provisional"):
            continue
        if config.dec(layer["qty_remaining"]) > 0:
            return config.dec(layer["unit_cost"])
    return None


def estimate_provisional_cost(row: dict, bucket: list[dict]) -> tuple:
    """临时层估算成本：退货自带单价 → 同物料同仓最新层成本 → 配置兜底。

    返回 (单价 Decimal, 来源码)；没有任何可用口径时返回 (None, 原因码)。
    """
    own_price = config.dec(row.get("price"))
    if own_price > 0:
        return own_price, "RETURN_PRICE"
    reference = latest_layer_cost(bucket)
    if reference is not None and reference > 0:
        return reference, "LATEST_LAYER"
    if config.PROVISIONAL_FALLBACK_COST > 0:
        return config.PROVISIONAL_FALLBACK_COST, "FALLBACK"
    return None, "RETURN_NO_COST_BASIS"


def on_hand(layers: list[dict]) -> Decimal:
    return sum((config.dec(layer["qty_remaining"]) for layer in layers), Decimal("0"))


def layer_value(layer: dict) -> Decimal:
    return config.dec(layer["qty_remaining"]) * config.dec(layer["unit_cost"])


def consume(layers: list[dict], qty: Decimal, seg_prefix: str,
            seg_counter: list[int]) -> list[dict] | None:
    """按最早层消耗；在手不足时返回 None 且不改动任何层（整行拒绝）。"""
    if on_hand(layers) < qty:
        return None
    remaining = qty
    segments: list[dict] = []
    for layer in layers:
        if remaining <= 0:
            break
        available = config.dec(layer["qty_remaining"])
        if available <= 0:
            continue
        take = min(available, remaining)
        segments.append(
            {
                "seg_id": f"{seg_prefix}{seg_counter[0]:06d}",
                "layer_id": layer["layer_id"],
                "qty": take,
                "unit_cost": config.dec(layer["unit_cost"]),
                "amount": take * config.dec(layer["unit_cost"]),
            }
        )
        seg_counter[0] += 1
        layer["qty_remaining"] = available - take
        remaining -= take
    return segments


def plan_restore(segments: list[dict], returnable: dict, qty: Decimal) -> list[tuple] | None:
    """按原消耗顺序回原层；超过可恢复数量时返回 None。"""
    remaining = qty
    plan: list[tuple] = []
    for segment in segments:
        if remaining <= 0:
            break
        left = returnable.get(segment["seg_id"], Decimal("0"))
        if left <= 0:
            continue
        take = min(left, remaining)
        plan.append((segment, take))
        remaining -= take
    if remaining > 0:
        return None
    return plan


def apply_restore(plan: list[tuple], layers_by_id: dict, returnable: dict,
                  seg_prefix: str, seg_counter: list[int]) -> list[dict]:
    restored: list[dict] = []
    for segment, take in plan:
        layer = layers_by_id[segment["layer_id"]]
        layer["qty_remaining"] = config.dec(layer["qty_remaining"]) + take
        returnable[segment["seg_id"]] = returnable.get(
            segment["seg_id"], Decimal("0")) - take
        restored.append(
            {
                "seg_id": f"{seg_prefix}{seg_counter[0]:06d}",
                "layer_id": segment["layer_id"],
                "qty": take,
                "unit_cost": segment["unit_cost"],
                "amount": take * segment["unit_cost"],
            }
        )
        seg_counter[0] += 1
    return restored
