# 外汇、利率与信用研究

`quant-macro` 是本仓库 `macro/` 下的独立 Python 3.12 研究应用，运行时仅依赖标准库。
外汇、国债利率、公司债与债券 ETF 共用输入身份、历史可知时间和报告协议。
本模块使用自有的研究现金账户，不属于根目录 `qfs_certified` 的 QExec 认证路径。
期货换月由 `international/qfs_global` 的 QDK/QExec 路径承担，新增产品规则检查层。
核心估值与账户不依赖第三方；期货入口另需安装 `international/requirements.lock` 和该应用。

## 已实现的范围

| 研究线 | 输入及计算 | 输出 |
|---|---|---|
| 外汇现货 | 历史 bid/ask CSV 导入；明确 base/quote；利差、动量、持有策略；前一可知事件决策；下一条观察报价成交；买卖价差、费用、滑点、正负现金余额计息、杠杆限制 | 报价币种净值、两币种现金、逐币种过账、成交、持仓 |
| 外汇远期 | 观测远期价；相同到期日 MTM；报价币种贴现；明确 fixing 现金结算；到期后按最新可用信号展期；covered interest parity 辅助函数 | 成交、到期及展期事件、现金、MTM、净值 |
| 国债、公司债 | 显式现金流；净价／全价／应计息；连续复利零息曲线插值；z-spread 反解；DV01、有效久期、凸性及平行冲击 | 逐券估值、现金流、风险和情景表 |
| 国债及短端利率期货 | 输入 CTD、转换因子、每手面值；CTD 近似 DV01 和毛基差；100-minus-rate 短端期货的隐含利率、每基点价值和冲击损益 | 利率期货风险表；历史账户回放使用既有期货引擎 |
| 债券 ETF | 历史可知的成分券面值、份额、现金、负债；dirty-price 篮子估值；折溢价；组合久期／DV01；陈旧和缺失报价的拒绝或显式模型估值 | 篮子指标、逐成分券价值及估值依据 |
| 多币种组合 | 多货币对共享现金账本；跨汇率一致性；逐币种融资；共同营业日 T+n；未结算应收应付；利差／动量策略 | 总净值、分币种余额、结算、成交、敞口 |
| 债券／ETF 账户 | 按结算日全价成交；票息、兑付、ETF 除息应收与派息；配置再投资；ETF 与成分券相对价值交易；借券限制、费用、滑点与融资 | 净值、成交、待结算持仓、现金与公司行为、联动信号 |
| 公开真实数据 | ECB 汇率历史、Fed GSW 连续零息曲线、iShares 持仓文件导入；来源原文件和哈希保留 | 历史变化、零息风险冲击、数据覆盖；均标为参考数据 |

## 运行

从 `macro/` 创建独立环境，避免改变根目录认证环境：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --no-deps -r requirements.lock
.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
.venv\Scripts\python -m pip check
.venv\Scripts\python -m quant_macro.cli demo --output demo-bundle
.venv\Scripts\python -m quant_macro.cli preflight --bundle demo-bundle --config examples/fx-spot.json
.venv\Scripts\python -m quant_macro.cli run --bundle demo-bundle --config examples/fx-spot.json --output result-fx
.venv\Scripts\python -m quant_macro.cli run --bundle demo-bundle --config examples/fx-forward.json --output result-forward
.venv\Scripts\python -m quant_macro.cli run --bundle demo-bundle --config examples/rates.json --output result-rates
.venv\Scripts\python -m quant_macro.cli run --bundle demo-bundle --config examples/credit.json --output result-credit
.venv\Scripts\python -m quant_macro.cli verify --output result-credit
```

Linux 使用 `.venv/bin/python`。每个输出目录必须不存在。预检只读取数据并在内存中计算，
也会检查远期整个持有窗口的报价与 fixing；失败时不产生研究输出。
`verify` 检查文件集合、SHA-256、原始输入身份，并从冻结输入和参数重新计算所有表格。
这是本版本的可复现性检查，不是第三方来源认证或密码学签名。

历史 CSV 的格式及债券输入结构见 [DATA_CONTRACT.md](DATA_CONTRACT.md)。
真实数据由用户依法取得后导入；本次提交的演示数据全部为合成数据。
ECB 参考汇率是信息用途数据，不能包装成真实可成交的买卖报价。

## Studio 接入

配套 `quant-studio` 增加以下模板：

- `macro-fx-research`：现货／远期，运行环境键 `quant-macro`。
- `macro-rates-research`：国债曲线和利率期货风险，运行环境键 `quant-macro`。
- `macro-credit-research`：公司债和债券 ETF，运行环境键 `quant-macro`。
- `macro-fx-portfolio`、`macro-bond-replay`、`macro-etf-relative-value`、`macro-reference-history`：运行环境键 `quant-macro`。
- `fx-futures-research`、`rates-futures-research`：运行环境键 `quant-macro-futures`，安装宏观模块及已有期货引擎。

在 Studio 设置中把 `quant-macro` 指向本应用的解释器。源码仍位于
`QUANT_WORKSPACE_ROOT/quant-futures-spread`。分别设置 `QUANT_MACRO_FX_BUNDLE`、
`QUANT_MACRO_RATES_BUNDLE`、`QUANT_MACRO_CREDIT_BUNDLE`，或在运行时选择对应数据包。
期货入口使用 `qdk.derivatives/v1`，不能使用本模块的 `data.json` 数据包。

支持 6E、ZN、SR3、TS／TF／T／TL 的产品单位规则检查；历史合约仍需提供真实合约乘数、
最小跳动、结算价、保证金、挂牌和到期时刻、规则来源。没有这些资料时不能据此声称
真实市场覆盖已经完成。规则层校验后进入已有引擎，拒绝错误单位和 off-tick 价格；
TS 校验 2023-11-07 的最小跳动切换。保证金与交割日期不由当前规则反推历史。

## 约定与当前边界

- `fx` 保留单货币对模型；`fx_portfolio` 使用共享现金、共同营业日及显式结算滞后。
  组合日历须提供起止日、节假日和来源，不自动假定法定假日；无跨币种抵押或信用额度模型。
- 利息使用上一条已可知的年化简单利率及其 ACT/360 或 ACT/365 基准。
  负现金余额叠加融资利差；隔夜间隔以实际秒数计算。现货最后一条报价平仓。
- 远期为有显式到期 fixing 的现金结算研究；存续期间必须有同一到期日的观测 bid/ask。
  不用理论远期价伪造市场成交。不实现抵押品、NDF 当地 fixing 日历或提前终止。
- 债券支持固定、正或零息、到期一次还本的现金流，价格与 DV01 统一为每 100 面值。
  当前仅支持付息日等于应计期末；零息曲线显式锚定结算日，禁止超过最后节点外推。
  不实现浮息、违约回收、提前赎回、回售或债券除息期；账户结算使用显式日历。
- `ACT/ACT-ICMA` 的应计比例使用显式单个正常付息期实际天数；不声称覆盖复杂长短首期。
  其他支持的应计比例为 ACT/365F、ACT/360 和 30E/360。
- z-spread 包含多种市场溢价，不能直接解释为纯违约风险或 OAS。
  国债期货风险近似来自指定 CTD，不自动寻找或预测交割选择权。
- ETF 估值按声明时点的持仓及份额计算，含现金和负债。任何模型替代的成分均在结果中标记。
  不等于发行人官方 NAV，也不证明实际申赎套利可以执行。
- `bond_replay` 提供账户回放和 ETF 二级市场联动；不模拟一级市场 AP 申赎。
  单个账户使用一个币种，债券单位为面值、ETF 为股数，允许小数仓位。
  窗口必须包含所有结算及已产生分红应收的付款日期，不能静默丢掉尾部义务。

## 新增账户和公开数据命令

```powershell
python -m quant_macro.cli run --bundle demo-bundle --config examples/fx-portfolio.json --output portfolio
python -m quant_macro.cli run --bundle demo-bundle --config examples/bond-replay.json --output bonds
python -m quant_macro.cli run --bundle demo-bundle --config examples/etf-relative-value.json --output relative-value
python -m quant_macro.cli download-public --provider ecb --start 2025-01-01 --output ecb-bundle
python -m quant_macro.cli download-public --provider fed-gsw --start 2025-01-01 --output fed-bundle
python -m quant_macro.cli import-public --provider lqd-holdings --source-file holdings.csv --output holdings-bundle
python -m quant_macro.cli run --bundle fed-bundle --config examples/reference-history.json --output history
python -m quant_macro.cli seal --source-file authorized-data.json --metadata provenance.json --config research.json --output authorized-bundle
```

公开下载不含历史可成交 bid/ask 或原始发布时间，不自动构造价差、融资、信用条款。
`reference_history` 报告使用实际下载版本；`known_at` 为本次取得时间，禁止包装成历史 PIT。
Fed GSW 使用 **SVENY** 连续零息利率并从百分数转换为小数，绝不把 SVENPY 平价利率当零息。
iShares 下载若拒绝访问，保留本地 CSV 导入入口，不绕过网站访问控制。

## 检查

```powershell
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
```

测试包括解析债券价格／久期／凸性、利差反解、现货现金守恒、未来数据扰动隔离、
远期相同到期报价及明确 fixing、ETF 全价篮子、陈旧数据、不可覆盖输出和重新计算检测篡改。
CI 工作流为 `.github/workflows/macro.yml`，本地结果见 [VALIDATION.md](VALIDATION.md)。

## 公式资料

- [TreasuryDirect：定价和利率](https://www.treasurydirect.gov/marketable-securities/understanding-pricing/)。
- [CME：国债期货转换因子](https://www.cmegroup.com/trading/interest-rates/calculating-us-treasury-futures-conversion-factors.html)。
- [CME：国债期货日历价差中的 DV01](https://www.cmegroup.com/education/articles-and-reports/treasury-futures-calendar-spreads-with-tails)。
- [ECB：参考汇率用途与限制](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)。
