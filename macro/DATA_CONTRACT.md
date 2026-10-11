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
后者也禁止进入多币种和债券／ETF 交易账户。
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

## 多币种外汇账户（0.2）

`data.fx_portfolio`：`currencies,pairs,observations`。完整样例在
`src/quant_macro/account_demo.py`，也包含在 `demo` 命令的输出中。

- `currencies` 按币种索引：`rate_basis` 为 360 或 365；`calendar` 为
  `start,end,holidays,source`，周末固定休市。共同营业日之外禁止成交和结算。
- `pairs`：`instrument_id,base,quote,notional_base,settlement_lag`。
- 每个 observation：`observed_at,available_at,rates_available_at,rates,quotes`。
  rates 必须覆盖所有账户币种；quotes 按 pair ID 提供 `bid,ask`，所有对同步发布。
- 净值换算从报告币种构建连通 mid 图，交叉汇率必须落在相应 bid/ask 中。
  现金按币种共用，不按货币对重复计息；未结算本金计入净值但不计入计息现金。
- 日步进顺序：按前日已知利率计息、当日结算、下一观察报价成交和估值。
  最后 close_on 之后只平仓；样本尾部必须足够完成所有 T+n 结算。
  残余外币利息保留并按明确汇率估值，不虚称所有外币余额归零。

## 债券／ETF 账户（0.2）

`data.bond_replay` 必需 `currency,calendar,instruments,observations,distributions,baskets`。
每个 instrument 有 `instrument_id,kind,available_at,settlement_lag`；kind 为 bond/etf，
bond 另有与估值模块相同的 `terms`。条款必须在窗口开始前已知。

每个 observation 有 `observed_at,available_at,funding_rate,rates_available_at,quotes,borrow`。
quotes 按 instrument ID 索引，含 `bid,ask,observed_at,available_at,kind`；kind 限 dealer/exchange。
单券净价按每 100 面值报价，ETF 按每股。borrow 每项为 `available_at,max_units,rate`。
做空必须提供可知借券容量和年化成本；持有期间容量不足即拒绝整次研究。

distributions 只用于 ETF：`instrument_id,ex_date,pay_date,amount,available_at`，amount 为每股现金。
除息日根据前一时点经济持仓产生应收或空头代付，付款日入账。
债券按实际已结算持仓领取／代付票息，支付顺序先付息赎回再结算当日交易。
不支持债券除息期和交收失败；临近到期而结算日已越界的交易会被拒绝。

baskets 每项为 `instrument_id,observed_at,available_at,faces_per_share,cash_per_share`。
**这是每股 ETF 对应的面值和现金**，不能与估值模块整个基金持仓的口径混用。
相对价值策略用上一时点全价篮子溢价决定 ETF 与成分券方向，下一次新鲜报价成交。
缺失报价可在 max_mark_age_days 内用于标记持仓，不能作为交易执行依据。

所有历史 JSON 可以先用 `seal` 校验并生成来源清单。元数据为来源清单去除
`schema_version,data_sha256` 后的六个字段；程序计算哈希，不接收伪造的预先成功标志。

## 公开参考历史（0.2）

`import-public` 和 `download-public` 保留 `source-raw.bin` 及导入回执，
`data.reference` 含 `provider,raw_sha256,records`。每条记录保留日期、序列 ID、
数据种类、单位、取得时间；缺测值不前向填充，重复时点拒绝。
报告从冻结的规范化记录复算；来源真实性及供应商后续修订不在校验范围。
目前 ECB / Fed 已通过真实下载验收；iShares 只有格式及合成样例验收，需合法 CSV 文件。

## 期货规则入口（0.2）

运行 `quant_macro.futures_cli preflight/run/verify`，参数形式与既有期货 CLI 相同，
配置 product 为 6E、ZN、SR3、TS、TF、T、TL。输入是 `qdk.derivatives/v1`。
报价必须是十进制价格；ZN 如 `112-165` 用 `treasury_decimal` 转成 112.515625。
日结算价可比成交 tick 精细；生命周期、历史保证金、规则来源由数据包提供。
固定版本 tick 跨历史规则变更的包应先拆分；不同结算币种必须分账户。
不启用原国内实盘开关，研究通道可独立执行国债期货回放。
