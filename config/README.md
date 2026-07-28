# config 目录说明

```
config/
  strategies.yaml              策略注册表（当前仅两个团队示例）
  strategy_instances/*.yaml    manual 模式品种实例（跨品种 example 用）
  backtest_*.yaml              回测运行配置
  future_list.csv
```

## 回测配置

| 文件 | 用途 |
|------|------|
| `backtest_example_dom_sub.yaml` | 团队示例：主力×次主力（`calendar_dom_sub`） |
| `backtest_example_cross_product.yaml` | 团队示例：跨品种（`manual` + instances） |

```bash
python run_backtest.py --config config/backtest_example_dom_sub.yaml
python run_backtest.py --config config/backtest_example_cross_product.yaml
```

`strategy:` 可选值：`example_dom_sub` `example_cross_product`

## strategy_instances

**有用，不能删。** `example_cross_product` 在 `manual` 模式下通过 instances 展开多个跨品种 symbol；`example_dom_sub` 走 calendar，不需要 instances 文件。

## Universe mode

| mode | 说明 |
|------|------|
| `manual` | 手填 symbol（跨品种 example） |
| `calendar_dom_sub` | 主力表 dom/sub，每 product 一实例（dom/sub example） |

见 [docs/universe设计.md](../docs/universe设计.md)。

## 字段约定

- `data.source`: `csv_spread` | `csv_catalog`
- `dom_table_dir`: 清洗后主力合约表目录（calendar 模式）
- `roll.on_switch`: `defer_until_flat`（calendar 模式）
- `jobs`: 并行 product 实例数（默认 1）
