# 外汇、利率与信用研究

`quant-macro` 是本仓库 `macro/` 下的独立 Python 3.12 研究应用，运行时仅依赖标准库。
外汇、国债利率、公司债与债券 ETF 共用输入身份、历史可知时间和报告协议。
本模块使用自有的研究现金账户，不属于根目录 `qfs_certified` 的 QExec 认证路径。
期货换月仍由 `international/qfs_global` 的 QDK/QExec 路径承担。

## 已实现的范围

| 研究线 | 输入及计算 | 输出 |
|---|---|---|
| 外汇现货 | 历史 bid/ask CSV 导入；明确 base/quote；利差、动量、持有策略；前一可知事件决策；下一条观察报价成交；买卖价差、费用、滑点、正负现金余额计息、杠杆限制 | 报价币种净值、两币种现金、逐币种过账、成交、持仓 |
| 外汇远期 | 观测远期价；相同到期日 MTM；报价币种贴现；明确 fixing 现金结算；到期后按最新可用信号展期；covered interest parity 辅助函数 | 成交、到期及展期事件、现金、MTM、净值 |
| 国债、公司债 | 显式现金流；净价／全价／应计息；连续复利零息曲线插值；z-spread 反解；DV01、有效久期、凸性及平行冲击 | 逐券估值、现金流、风险和情景表 |
| 国债及短端利率期货 | 输入 CTD、转换因子、每手面值；CTD 近似 DV01 和毛基差；100-minus-rate 短端期货的隐含利率、每基点价值和冲击损益 | 利率期货风险表；历史账户回放使用既有期货引擎 |
| 债券 ETF | 历史可知的成分券面值、份额、现金、负债；dirty-price 篮子估值；折溢价；组合久期／DV01；陈旧和缺失报价的拒绝或显式模型估值 | 篮子指标、逐成分券价值及估值依据 |

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
- `fx-futures-research`、`rates-futures-research`：复用 `quant-futures-global` 环境及已有期货引擎。

在 Studio 设置中把 `quant-macro` 指向本应用的解释器。源码仍位于
`QUANT_WORKSPACE_ROOT/quant-futures-spread`。分别设置 `QUANT_MACRO_FX_BUNDLE`、
`QUANT_MACRO_RATES_BUNDLE`、`QUANT_MACRO_CREDIT_BUNDLE`，或在运行时选择对应数据包。
期货入口使用 `qdk.derivatives/v1`，不能使用本模块的 `data.json` 数据包。

国内 TS／TF／T／TL、海外国债、短端利率及 FX 合约均需提供真实合约乘数、
最小跳动、结算价、保证金、挂牌和到期时刻、规则来源。没有这些资料时不能据此声称
真实市场覆盖已经完成。新增期货模板是已有引擎的用途入口，不是新增交易所适配器。

## 约定与当前边界

- 现货账户每次处理一个货币对，保留两个币种的现金，并只在显式 mid 汇率上换算净值。
  不合并不同货币对账户，不实现真实交割日历、跨币种保证金或信用额度。
- 利息使用上一条已可知的年化简单利率及其 ACT/360 或 ACT/365 基准。
  负现金余额叠加融资利差；隔夜间隔以实际秒数计算。现货最后一条报价平仓。
- 远期为有显式到期 fixing 的现金结算研究；存续期间必须有同一到期日的观测 bid/ask。
  不用理论远期价伪造市场成交。不实现抵押品、NDF 当地 fixing 日历或提前终止。
- 债券支持固定、正或零息、到期一次还本的现金流，价格与 DV01 统一为每 100 面值。
  当前仅支持付息日等于应计期末；零息曲线显式锚定结算日，禁止超过最后节点外推。
  不实现浮息、违约回收、提前赎回、回售、除息期或清算结算日历。
- `ACT/ACT-ICMA` 的应计比例使用显式单个正常付息期实际天数；不声称覆盖复杂长短首期。
  其他支持的应计比例为 ACT/365F、ACT/360 和 30E/360。
- z-spread 包含多种市场溢价，不能直接解释为纯违约风险或 OAS。
  国债期货风险近似来自指定 CTD，不自动寻找或预测交割选择权。
- ETF 估值按声明时点的持仓及份额计算，含现金和负债。任何模型替代的成分均在结果中标记。
  不等于发行人官方 NAV，也不证明实际申赎套利可以执行。
- 固定收益当前交付估值与风险研究，未实现单券成交、票息再投资和 ETF 套利账户回测。

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
