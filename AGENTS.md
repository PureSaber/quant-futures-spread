# quant-futures-spread

Public futures spread research engine. The certified path is fixture-only and backtest-only; public visibility does not authorize live broker integration, credentials, or real orders.

## Commands

```bash
pip install -e ".[dev]"
python run_backtest.py --config config/backtest_example_dom_sub.yaml
python -m pytest tests/ -q
ruff check .
ruff format --check qfs_certified tests/test_certified_backtest.py tests/test_coverage_factor_strategies.py tests/test_coverage_panels_research_utils.py
python -m pytest --cov=. --cov-branch --cov-report=term-missing --cov-fail-under=80 -q
```

## M4认证边界

- `qfs_certified/`和`config/certified_local_sample_v1.yaml`是fixture-certified、backtest-only路径；禁止加入live broker、凭据或真实订单能力。
- 认证策略只能通过QExec`Strategy.on_event`返回腿级`OrderIntent`。QExec`DeterministicRunEngine`、`RuleBookRiskGate`、匹配模型和`ExactAccountLedger`是订单、成交、现金、持仓、保证金及NAV的唯一认证事实来源。
- `core/`、`strategy/`中的现有撮合与会计必须保留为legacy/research-only，不得用于生成认证事实。
- `data/local_sample/certified_v1/`只描述仓内合成样例的PIT fixture，不声明真实上市历史或真实交易所参数。
- standard/v2保证金文件只能分解QExec snapshot；逐品种之和必须对每个snapshot精确等于QExec initial/maintenance aggregate，否则fail closed。禁止维护第二套保证金状态。
- 内部依赖固定为QDK`v0.8.1`、QExec`v0.5.1`、QLab`v0.3.1`，认证验证不得改用浮动分支或相邻工作树。

## Data paths

Set `QUANT_FUTURES_DATA_ROOT` or copy `config/data_paths.example.yaml` to a local override.
Default data root: `D:/data`.

## Related

- [quant-report-hub](https://github.com/PureSaber/quant-report-hub) spread adapter reads `output/`
- [quant-lab](https://github.com/PureSaber/quant-lab) indexes runs from `output/`
