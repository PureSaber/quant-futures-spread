# quant.macro.bundle/v1

数据目录包含 `manifest.json` 和 `data.json`，文件不得链接到目录外。
所有数值必须有限；币种为三个大写字母；利率为小数；日期为 `YYYY-MM-DD`；
时间戳必须有时区。重复 JSON 键、交叉报价、未知字段和重复观察会被拒绝。

## 来源清单

```json
{
  "schema_version": "quant.macro.bundle/v1",
  "evidence_kind": "retrospective",
  "source": "供应商／本地导出名称与数据范围",
  "license": "授权或公开使用条款说明",
  "retrieved_at": "2026-10-11T00:00:00+00:00",
  "data_sha256": "data.json 原始字节的 SHA-256",
  "quote_use": "observed_bid_ask",
  "limits": "延迟、缺失、报价性质及其他已知限制"
}
```

`evidence_kind` 只能为 `synthetic` 或 `retrospective`，`quote_use` 只能为
`observed_bid_ask` 或 `reference_only`。后者禁止用于外汇账户回放。
来源描述和授权文字是输入声明，程序无法验证其真实性。

## 外汇历史 CSV

必需列：`observed_at,available_at,bid,ask,base_rate,quote_rate,rates_available_at`。
可选列：`fixing`（到期实际 fixing）、`forwards`（JSON 数组）。每条远期记录为
`{"maturity":"2026-02-05","bid":"1.101","ask":"1.102"}`。
远期列表对应当前这条观察，必须包含存续头寸的相同到期日。

伴随元数据 JSON 必须含 `base`、`quote`、`base_rate_basis`、`quote_rate_basis`、
`source`、`license`、`retrieved_at`、`limits`、`evidence_kind`、`quote_use`。
两项 rate_basis 分别为数字 360 或 365。

```powershell
python -m quant_macro.cli import-fx --csv quotes.csv --metadata fx-metadata.json --output fx-bundle
```

CSV 导入保留原始发布时间和数值文字，不补造日期、利率或价差。超过默认观察间隔
7 天或发布时间晚于观察 24 小时的输入会在导入时拒绝；研究配置还可进一步收紧。
`data.json.fx` 是相同记录的 JSON 形式，包含上述四项货币元信息和 `quotes` 数组。
next quote 的观察时刻必须晚于上一条信号可知时刻。

## 固定收益结构

`data.json.fixed_income` 必需数组为 `bonds`、`curves`、`bond_quotes`；可选数组为
`etfs`、`etf_quotes`、`rate_futures`。完整可运行结构由 `quant_macro.cli demo` 生成，
源码样例在 `src/quant_macro/demo.py`。

`bonds` 每项包含：

- `available_at`：合约条款何时已知。
- `sector`：`government` 或 `corporate`。
- `terms`：`instrument_id,currency,day_count,periods`。
- 每个 `periods` 项有 `start,end,payment,coupon,principal`。金额均为每 100 面值，
  coupon 是本期确切金额而非年化票息率。付息期连续排序；最后一期归还 100 本金。

`curves` 每项有 `observed_at,available_at,currency,anchor,nodes`。anchor 必须等于
研究 settlement。nodes 为 `years,zero_rate` 数组，使用连续复利、ACT/365F 时间，
从 years=0 严格递增。不要直接把平价收益率曲线放入 zero_rate。

`bond_quotes` 及 `etf_quotes` 每项有 `instrument_id,observed_at,available_at,bid,ask,kind`。
债券为每 100 面值净价，ETF 为每份价格。kind 为 `trade`、`dealer` 或 `valuation`；
估值报价即使新鲜也不会标记为市场成交报价。

`etfs` 每项有 `instrument_id,currency,observed_at,available_at,shares,cash,liabilities,holdings`。
shares 为该快照基金总份额；cash/liabilities 为整个基金的金额；每个 holding 是
`instrument_id,face`，face 为整个基金持有的该债券面值。不可混用申赎单位持仓和基金总份额。
成分、份额及现金负债必须来自同一时点快照。未知成分或重复成分终止研究。

`rate_futures` 每项有 `instrument_id,kind,currency,observed_at,available_at,rule_source`。
国债类 kind=`treasury`，另需 `ctd_id,conversion_factor,face_per_contract,price`，
price 为十进制每 100 报价（32 分之一等报价需在来源导出时转换）。
短端类 kind=`stir`，另需 `price,value_per_price_point`，使用 `100 - 利率百分数` 报价。
每手基点价值为 value_per_price_point × 0.01。

## 历史可知时间和缺失处理

as_of 之后发布的数据不进入该时点估值。历史修订按 observation 再 publication 排序，
旧观察的新修订不会替代更新观察。输入结构、币种、日期和数值仍须有效。
曲线、成分持仓和利率期货报价的陈旧均终止研究。
债券／ETF 报价默认拒绝缺失或陈旧；`stale_policy=model` 时，单券使用用户指定
model_spread 显式估值，ETF 市场价格陈旧时不计算折溢价。

## 输出

共同文件为 `study.json,config.json,input-data.json,input-raw.json,input-manifest.json,report.html,artifacts.json`。
外汇另有 `nav.csv,fills.csv,positions.csv,ledger.csv,rolls.csv`；固定收益另有
`valuations.csv,cashflows.csv,scenarios.csv,rate_futures.csv,etfs.csv,components.csv`。
空表保留 empty 表头。CSV 原始精度用于复算，HTML 每表最多展示 200 行。
每个研究包冻结输入和参数；校验器拒绝更改产物后仅重写哈希清单的做法。
