# 期货价差回测框架 (quant-futures-spread)

Private research repo on GitHub: `PureSaber/quant-futures-spread`.

本仓库同时保留原有价差研究回测和独立的M4认证回测。原有`core/`、`strategy/`路径属于
legacy/research-only，不是认证成交、资金、持仓或NAV的事实来源；`qfs_certified/`才是
fixture-certified入口，且只支持确定性backtest，不含live broker、凭据或真实下单路径。

## 环境

```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

`pyproject.toml`冻结安装QDK`v0.5.0`、QExec`v0.2.0`、QLab`v0.3.0`；不要用浮动
`main`或相邻工作树替代认证依赖。

数据路径：设置环境变量 `QUANT_FUTURES_DATA_ROOT`，或参考 `config/data_paths.example.yaml`。
默认 `D:/data`；本机访问不到要先改路径。

## 跑示例

```bash
# 主力 × 次主力（按主力表换月）
python run_backtest.py --config config/backtest_example_dom_sub.yaml

# 跨品种（手填 symbol）
python run_backtest.py --config config/backtest_example_cross_product.yaml
```

以上两个命令走保留的legacy/research-only会计路径。M4认证样例使用：

```bash
qfs-certified-backtest --config config/certified_local_sample_v1.yaml --output-root output/certified
```

认证策略仅通过`Strategy.on_event`发出稳定的腿级`OrderIntent`。订单依次经过QExec
`DeterministicRunEngine`、`RuleBookRiskGate`、`BarMatchingModel`和`ExactAccountLedger`；
信号不能直接修改仓位，仓内`ReconcileSimulator`、`BacktestPositionBook`和旧会计均不参与
认证成交、现金、保证金或NAV计算。

`data/local_sample/certified_v1/`是版本化PIT fixture，覆盖仓内local sample出现的合约标签，
并显式记录交易场所、CNY、乘数、tick、数量步长、保证金、手续费、开平/平今、夜盘交易日、
每日结算和换月元数据。这些值只适用于合成fixture区间，`historical_claim=none`，不得解释为
交易所真实上市历史或真实合约参数。

legacy/research-only结果在`output/<run_id>/`：`daily/`净值、`trades/trades.csv`、calendar模式还有
`rolls/roll_events.csv`。同时写入`standard/`标准运行契约，包含收益、持仓、订单、成本、
暴露、指标和SHA-256清单，可由`quant-lab validate --run-dir output/<run_id>`验证。

认证结果由QLab`write_standard_run_v2(profile="backtest-ledger")`写入完整的
`returns`、`positions`、`portfolio_snapshots`、`exposures`、`orders`、`order_events`、
`fills`、`costs`、`cash_ledger`和`margin`，随后立即由
`load_and_validate_standard_run`回读。逐品种保证金只是QExec snapshot的报告分解：每个snapshot
都会与QExec aggregate initial/maintenance margin做精确恒等校验，任何metadata或舍入漂移都
会fail closed，不维护第二套保证金状态。

## 目录

```
strategy/strategies/   策略（本仓库只含两个 example）
strategy/framework/    Strategy、TargetOrder 契约
core/                  回测引擎：runner、撮合、记账、universe
qfs_certified/         fixture-certified QExec认证链
data_sources/          读 CSV
config/                strategies.yaml、backtest_*.yaml、future_list.csv
utils/                 注册表、合约工具、panel_registry
run_backtest.py        入口
```

## 两种套利怎么配

| 场景 | universe | symbol 从哪来 | 数据目录 |
|------|----------|-----------------|----------|
| 主力×次主力 | `calendar_dom_sub` | 主力表，runner 注入 | `套利数据-主力次主力` |
| 跨品种 | `manual` | `strategy_instances/*.yaml` 手填 | `套利数据-跨品种` |

对应策略：`example_dom_sub`、`example_cross_product`。细节见各策略文件头部注释和 `config/README.md`。

## 写新策略

1. 复制 `strategy/strategies/example_*` 改逻辑  
2. 在 `config/strategies.yaml` 注册  
3. 跨品种在 `strategy_instances/` 列 symbol；dom/sub 只写品种级 params  
4. 策略里只 `import framework` / `utils`，**不要** `import core`  
5. `on_bar` 返回当前时刻完整 `TargetOrder` 列表  

## 测试

```bash
ruff format --check qfs_certified tests/test_certified_backtest.py tests/test_coverage_factor_strategies.py tests/test_coverage_panels_research_utils.py
ruff check .
python -m pytest --cov=. --cov-branch --cov-report=term-missing --cov-fail-under=80 -q
```
