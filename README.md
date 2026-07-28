# 期货价差回测框架 (quant-futures-spread)

Private research repo on GitHub: `PureSaber/quant-futures-spread`.

本地历史数据上跑价差策略回测，策略代码与实盘仓库 FuturesSpread 同构，验证通过后可整目录复制过去改配置实盘。

## 环境

```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

数据路径：设置环境变量 `QUANT_FUTURES_DATA_ROOT`，或参考 `config/data_paths.example.yaml`。
默认 `D:/data`；本机访问不到要先改路径。

## 跑示例

```bash
# 主力 × 次主力（按主力表换月）
python run_backtest.py --config config/backtest_example_dom_sub.yaml

# 跨品种（手填 symbol）
python run_backtest.py --config config/backtest_example_cross_product.yaml
```

结果在 `output/<run_id>/`：`daily/` 净值、`trades/trades.csv`、calendar 模式还有 `rolls/roll_events.csv`。

## 目录

```
strategy/strategies/   策略（本仓库只含两个 example）
strategy/framework/    Strategy、TargetOrder 契约
core/                  回测引擎：runner、撮合、记账、universe
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
python -m pytest tests/ -q
```
