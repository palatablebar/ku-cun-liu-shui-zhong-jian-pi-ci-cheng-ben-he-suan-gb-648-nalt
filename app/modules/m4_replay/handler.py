"""重放入口：单独跑一遍时序重放，返回层数、消耗段与数量分项。"""
from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Request

from ... import config, pipeline
from ...errors import DomainError
from . import service

router = APIRouter(prefix="/api/replay", tags=["时序重放"])


@router.post("")
def run_replay(request: Request, policy: str = ""):
    settings = request.app.state.settings
    store = request.app.state.store
    if not policy or policy == settings.return_policy:
        # 同策略：读本批登记文件原地重算，保持 M4 既有先删后插路径。
        return service.apply(settings, store, store.latest_run())
    if policy not in config.RETURN_POLICIES:
        raise DomainError(
            "E_RETURN_POLICY",
            f"退货策略只支持 {config.RETURN_POLICIES}", 400)
    # 换策略：策略进入 run_id，整链重跑生成独立新批次，原批次完整保留。
    run_settings = replace(settings, return_policy=policy)
    return pipeline.run_chain(run_settings, store)


@router.get("")
def replay_state(request: Request):
    store = request.app.state.store
    run = store.latest_run()
    if not run:
        return {"run_id": None, "layers": 0, "segments": 0}
    return {
        "run_id": run["run_id"],
        "layers": len(store.layers(run["run_id"])),
        "segments": len(store.consumptions(run["run_id"])),
        "quantities": {
            "opening": run["opening_qty"],
            "in": run["in_qty"],
            "out": run["out_qty"],
            "adj_in": run["adj_in_qty"],
            "adj_out": run["adj_out_qty"],
            "ending": run["ending_qty"],
        },
        "cogs": run["cogs"],
        "ending_value": run["ending_value"],
    }
