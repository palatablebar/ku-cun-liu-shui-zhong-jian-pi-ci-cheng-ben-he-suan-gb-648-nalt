# 运行手册 stockflow

## 启动

```bash
docker compose up -d --build
curl -s localhost:18207/api/health
```

首页 `http://localhost:18207/` 打开后点「重跑」执行样例主流程；所有页面共用同一批次。

## 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

`down -v` 删除命名卷 `stockflow-data`；启动后第一次运行生成新库。

## 主流程

```bash
curl -s -X POST localhost:18207/api/demo/run | python3 -m json.tool | head -40
curl -s localhost:18207/api/quarantine.csv | head -5
curl -s localhost:18207/api/replay -X POST
curl -s localhost:18207/api/cost | python3 -m json.tool | head -30
curl -s localhost:18207/api/reconcile | python3 -m json.tool | head -40
curl -s localhost:18207/api/export/manifest.json
```

## 导入自己的文件

```bash
curl -s -F sample=movements_clean.csv localhost:18207/api/movements/import
curl -s -F file=@/path/to/movements.csv localhost:18207/api/movements/import
curl -s -F file=@/path/to/opening.csv localhost:18207/api/movements/opening
```

导入只接受与样例同列名（或登记过的英文别名）的文件；列名不对时首行会被判表头缺失。

## 盘点提交与版本

```bash
curl -s -X POST localhost:18207/api/reconcile/counts \
  -H 'Content-Type: application/json' \
  -d '{"count_no":"PD-C-05","revision":2,"rows":[{"sku":"000123","warehouse":"WH-A","counted_qty":"500","unit":"kg"}]}'
```

- 旧版本返回 409 `E_STALE_COUNT_REVISION`；同版本不同内容返回 409 `E_COUNT_REVISION_CONFLICT`。
- 同版本同内容幂等，重复提交不新增盘点行。
- 盘亏超过在手返回记录状态 `COUNT_SHORT`，不做负库存。

## 退货原单晚到

售后仓退货记录先到、销售原单晚到时，两种策略二选一（进入 `run_id`，可在同一批输入间切换对比）：

| 策略 | 当天在库 | 成本口径 | 复核标记 |
|---|---|---|---|
| `provisional`（默认） | 退货按参考成本建临时层，当天入在库 | 临时估算，原单补到重放后回原批次层 | 流水/层标「临时」，退货台账记 `PROVISIONAL` |
| `wait_original` | 整行隔离，不入在库 | 严格，只在原单到后回原层 | 退货台账记 `PARKED`，原因码 `RETURN_DOC_NOT_FOUND` |

```bash
# 用等原单策略重跑，生成独立批次，原批次完整保留
curl -s -X POST 'localhost:18207/api/replay?policy=wait_original' | python3 -m json.tool
# 退货台账（页面 成本 → 退货复核台账 同源）
curl -s localhost:18207/api/cost/returns | python3 -m json.tool
curl -s localhost:18207/api/export/returns.csv
```

原单补到后，把流水里的关联单号改为真实原出库单号再导入重跑：临时层消失、数量回原批次层，
**在库数量不变、不重复增加库存或成本层**，出库成本按原批次单价重算；同一 `run_id` 重跑先删后插，幂等不重复。

落库在单事务内完成，中途失败整体回滚，上一完整批次与五张表数据原样保留，不会留下半成品。

## 测试

```bash
docker compose exec -T app python -m pytest -q
```

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 首页 200 但数字全 0 | 还没跑批次 | 点「重跑」或 POST `/api/demo/run` |
| 导入回报 404 | 样例名不在 `sample_data/` | 用上传文件或核对样例名 |
| `POST /api/replay` 409 | 批次登记的源文件已不在样例目录 | 重新导入流水 |
| 盘点提交 409 | 版本旧于已记录版本 | 升版本号再提交 |
| 层金额与期末金额不符 | 有人直接改库 | 重跑批次会先删后插，恢复一致 |
| 退货显示「临时入账」 | 原出库单还没到，先按参考成本入在库 | 原单补到后改关联单号重跑，自动回原层 |
| 退货进隔离区 `RETURN_DOC_NOT_FOUND` | 当前是 `wait_original` 策略或无参考成本 | 切 `provisional`，或补齐原单号/单价后重跑 |
| 重跑后页面数字没变 | 同输入同策略命中同一 `run_id`，本就幂等 | 换输入或 `?policy=` 才会生成新批次 |
