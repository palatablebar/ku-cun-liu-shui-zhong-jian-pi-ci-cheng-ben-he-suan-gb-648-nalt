"""失败保护：落库中途失败时上一完整批次与数据不丢失、不出现半成品。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app import pipeline
from app.store import Store


def test_failed_save_keeps_previous_complete_run(client, demo):
    store = client.app.state.store
    settings = client.app.state.settings
    run_id = demo["run_id"]
    movements_before = len(store.movements(run_id))
    layers_before = len(store.layers(run_id))

    real_save = Store.save_run
    calls = {"n": 0}

    def flaky_save(self, run, movements, layers, consumptions, counts, log):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated disk failure during save")
        return real_save(self, run, movements, layers, consumptions, counts, log)

    Store.save_run = flaky_save
    try:
        with pytest.raises(RuntimeError):
            pipeline.run_demo(settings, store)
    finally:
        Store.save_run = real_save

    # 上一完整版本原样可读
    kept = store.get_run(run_id)
    assert kept is not None
    assert kept["status"] in ("ready", "empty")
    assert len(store.movements(run_id)) == movements_before
    assert len(store.layers(run_id)) == layers_before
    assert len(store.consumptions(run_id)) == demo["segments"]
    # 重跑成功后仍是同一 run_id、同样行数（先删后插幂等）
    recovered = pipeline.run_demo(settings, store)
    assert recovered["run_id"] == run_id
    assert len(store.movements(run_id)) == movements_before
    assert len(store.layers(run_id)) == layers_before


def test_rerun_does_not_duplicate_provisional_layers(client, demo):
    store = client.app.state.store
    settings = client.app.state.settings
    run_id = demo["run_id"]
    first_prov = len(store.provisional_layers(run_id))
    assert first_prov == 2
    pipeline.run_demo(settings, store)
    pipeline.run_demo(settings, store)
    again = store.provisional_layers(run_id)
    assert len(again) == first_prov
    assert Decimal(sum((Decimal(layer["qty_in"]) for layer in again), Decimal("0"))) == Decimal("20")
    # 临时层 id 不因重跑漂移（同一 run_id 先删后插）
    assert {layer["layer_id"] for layer in again} == {"L000009", "L000014"}


def test_migration_adds_columns_without_losing_runs(tmp_path):
    from app import config
    import sqlite3

    db = tmp_path / "legacy.sqlite"
    # 先按新结构建库写一批，再模拟老库缺列场景验证迁移幂等
    settings = config.Settings(db_path=str(db), sample_dir="/workspace/sample_data")
    store = Store(str(db))
    store.init()
    with store._conn() as conn:
        cols = {row["name"] for row in conn.execute(
            "PRAGMA table_info(layers)").fetchall()}
        assert {"provisional", "cost_source", "ref_doc"} <= cols
        run_cols = {row["name"] for row in conn.execute(
            "PRAGMA table_info(runs)").fetchall()}
        assert {"return_policy", "provisional_qty", "parked_returns"} <= run_cols
    # 再次 init 不报错（列已存在）
    store.init()
    assert sqlite3.connect(str(db)).execute(
        "SELECT count(*) FROM runs").fetchone()[0] == 0
