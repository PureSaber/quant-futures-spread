# 全球股指与商品研究扩展

本扩展属于 `international` 研究子项目，使用原固定依赖。原 `qfs_certified` 认证入口及依赖不变。所有命令在安装本子项目后运行。

## 品种池与开关

```powershell
python -m qfs_global.research universe
```

包含 15 个股指、19 个能源/金属/农产品。每项带交易场所、币种和交易所参考链接。代码是内部研究标识，不直接充当供应商合约代码；合约乘数、tick、上市/最后交易日、到期日和历史保证金必须逐合约声明。

IF/IH/IC/IM 在该研究入口可用。旧 `future_list.csv` 的 `trade/tradeF` 不在此次改动范围；品种池不打开真实交易。`history_status=not_assessed` 表示品种目录不等于真实历史覆盖。

## 免费商品参考历史

```powershell
python -m qfs_global.research free-reference --symbols 'CL=F,NG=F,GC=F,SI=F,HG=F,ZC=F,ZW=F,ZS=F' --start 2020-01-01 --end 2025-12-31 --output commodity-reference
python -m qfs_global.cli verify --output commodity-reference
```

数据来自 Yahoo Finance 公开图表源，一次每代码一个有界请求，不请求付费接口或重试绕过访问限制。无额外依赖。供应商端点可改变或限流，失败不发布部分成功报告。

输出是独立 `qfs.vendor-reference/v1`，只能 `reference_analysis`，**不是** `qdk.derivatives/v1`。保留 OHLC、成交量、供应商时间、报价单位、原响应哈希及空值排除日期，不填补价格；负价格保留。`currency` 字段原样保存供应商报价单位，农产品可能是 **USX（美分）**，不能与 USD 自动混算。

这类供应商代码历史缺少逐行具体月份、可审计换月方式、结算价、乘数/保证金历史，不能用于实际合约成交、期限结构或换月盈亏验证。长价格序列不等于长期原始合约覆盖。数据仅保存在本地，代码仓不附带市场数据或再分发许可。

## 原始月份合约与分批历史

继续复用 QDK 的官方 NSE 指数期货、Cboe VX、授权 CSV 接入。Cboe VX 是波动率品种，不算商品覆盖。NSE 日文件入口为 [官方报告](https://www.nseindia.com/all-reports-derivatives)。例如：

```powershell
python -m quant_data_kit.derivatives.cli free-fetch --provider nse-fo --kind future --selection NIFTY25FEBFUT,NIFTY25MARFUT,BANKNIFTY25FEBFUT,BANKNIFTY25MARFUT --start 2025-01-08 --end 2025-01-17 --rights-note 'Personal research; no redistribution claim' --output nse-chain
python -m qfs_global.research merge-history --bundle chunk-a --bundle chunk-b --output combined
```

官方免费采集单批仍限定 32 天。合并保留原始价格、来源摘要及所有来源限制的交集；同一合约同一时刻一致记录去重，冲突行情或规则拒绝，不以新数据覆盖旧记录。

免费 NSE/Cboe 适配器按样本首日推断上市/条款可得时间，分批后会不同。如确认需要合并这类**回顾性假设**，可加 `--reconcile-first-observed`，只允许相同免费提供方、其他条款全部相同的合约取最早观察时间，并在来源清单明确记录。它不恢复真实上市历史，也不授予 PIT 认证；其他规则变化仍拒绝。

## 覆盖检查

```powershell
python -m qfs_global.research coverage --bundle combined --requirements requirements.json --output coverage.json
```

`requirements.json` 是对象列表，每项如下。`expected_sessions` 要替换成完整的交易所日历；阈值由研究设计决定，不是交易所标准。

```json
[{"venue":"NSE","product":"NIFTY","expected_sessions":["2025-01-08","2025-01-09","2025-01-10"],"calendar_source":"说明日历来源与区间","min_sessions":750,"min_contracts":12,"min_roll_overlaps":8,"min_overlap_sessions":5}]
```

报告列出实际日期、合约数、临近月份可成交量/结算价重叠、缺失交易日、结算缺失、负/零价格及保证金缺口。合成数据永远不能通过“真实历史覆盖”判断。品种层面每日至少一条行情不代表完整链；结构覆盖通过也不代表实际换月成交通过。

## 股指基差

```powershell
python -m qfs_global.research basis --bundle index-chain --observations spot-carry.json --output basis-report
```

`spot-carry.json` 为记录列表，字段：`instrument_id, at, available_at, spot_id, spot_currency, spot_at, spot_available_at, spot, funding_rate, dividend_yield, carry_known_at, max_skew_seconds, source`。合约 `underlying` 必须等于 `spot_id`。`at` 要精确匹配期货观察，`available_at` 是研究决策时点；所有输入必须已可得，现货与期货观察偏差不超过显式上限（最多一小时）。

理论值 `S × exp((r-q)T)`，T 用 ACT/365F；报告期现点差、比例、年化简单基差、隐含连续资金持有成本和偏离理论值。期现标识/币种不一致、异步价格、未来利率股息假设或缺失对应期货观察均拒绝。[CME 基差与公允价值说明](https://www.cmegroup.com/trading/equity-index/fairvalue.html)。基差残差未扣成分篮子复制、融资和借券约束，不等于可执行套利利润。

## 跨品种组合与换月

```powershell
python -m qfs_global.research portfolio --bundle chain --config examples/global/portfolio.json --output portfolio-report
python -m qfs_global.cli verify --output portfolio-report
```

示例 product 为合成 `DEMO`。真实数据要明确 `legs`，每腿配置 product、direction (+1/-1)、整数 contracts、nearby/calendar_spread、expiry/volume、roll_days。在一个 QExec 研究账本中合并腿级目标，上一条已知观察决策，下一观察开盘模拟成交，显式扣费和不利跳数滑点，最后一天平仓。初始资金及保证金要足够；`account` 复用现有配置，费用为每合约每次成交。

支持同币种、不同场所但观察严格同步的组合。跨币种、缺结算、重复日线、不同步价格、无保证金均拒绝。固定合约张数不是风险平价；无 SPAN 或组合保证金抵扣。

实物交割品种必须逐合约提供 `delivery_deadlines`：`{"合约ID":{"at":"带时区的强制退出时间","known_at":"规则可得时间","source":"规则来源"}}`。应使用首次通知日、交割风险截止日、最后交易日中最早的适用期限，不能只填到期日。选择在截止前退出；行情断档使下一观察跨过截止时，回放拒绝而不是假造平仓。

## 尚未补齐

长期 CME/ICE 原始合约链、完整历时规则/保证金、真实交割及可成交换月证据仍需可用的数据源。当前免费商品参考历史不能消除这些缺口。[CME 官方历史数据入口](https://www.cmegroup.com/datamine.html)仅作为后续来源参考；本扩展不购买数据。
