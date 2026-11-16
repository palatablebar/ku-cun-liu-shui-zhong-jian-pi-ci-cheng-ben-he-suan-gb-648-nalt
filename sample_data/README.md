# 样例说明

| 文件 | 用途 | 已知问题 |
|---|---|---|
| `movements_dirty.csv` | 主流程脏样例（35 行，含重复表头分段） | 乱序、重复表头、重复单行、同键版本冲突、负数量、调拨缺边、调拨数量不等、单位混用 g/kg、全角数字、千分位、空值哨兵、列数不齐、时间无法解析、截断末行 |
| `movements_clean.csv` | 干净对照（同一物料同一仓库的入库/出库/调拨/退货） | 无；与脏样例走同一条代码路径 |
| `opening_balance.csv` | 期初余额与期初单价 | 一条零数量期初（不建层）、一处 g/kg 混用 |
| `physical_counts.csv` | 实盘数量，用来生成调整层 | 一条相符、三条差异 |
| `movements_truncated.csv` | 末行截断 | 末行无换行且列数不足 |
| `movements_header_only.csv` | 仅表头 | 没有数据行 |
| `empty.csv` | 空文件 | 0 字节 |
| `movements_returns_late_day1.csv` | 退货原单晚到·当天 | TH-7103 指向晚到原单，默认策略下建临时层入在库 |
| `movements_returns_late_day2.csv` | 退货原单晚到·原单补到 | TH-7103 改指真实原单，重放后临时层消失、回原批次层 |
