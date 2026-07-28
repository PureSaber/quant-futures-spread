# 因子挖掘流水线

基于 `D:\data` 价差 CSV 的六大家族因子挖掘、IC 检验与可解释性报告。

## 快速开始

```bash
# Smoke（豆一 + OI&Y，约 1 分钟）
python research/pipeline/run_round.py --config research/config/factor_mining_smoke.yaml

# Round 1 全量（2024+2025，约 2374 spreads，30–90 分钟）
python research/pipeline/run_round.py --config research/config/factor_mining.yaml
```

# Round 2 成本感知筛选
python research/pipeline/run_round.py --config research/config/factor_mining_round2.yaml
```

## 产出

`research/output/<round_id>/`

| 文件 | 说明 |
|------|------|
| `universe_manifest.parquet` | 宇宙 spread 列表 |
| `factor_panel.parquet` | 因子 + 前瞻收益长表 |
| `ic_summary.csv` | 中间价 `fwd_ret` IC / ICIR |
| `ic_summary_realized.csv` | 可实现 `fwd_realized_long` IC / ICIR |
| `ic_compare_mid_vs_realized.csv` | 两套 IC 对比（含 ic_decay） |
| `selected_factors_realized.csv` | Round2 成本感知入选因子 |
| `ic_by_sector.csv` | 板块维度 IC |
| `selected_factors.csv` | 达阈值入选因子 |
| `interpretability_report.md` | 可解释性叙事报告 |

## 因子家族

A 均值回归 · B 动量 · C 波动 · D 微观结构 · E 期限结构 · F 跨品种

详见 `research/factors/registry.py`。

## Top5 单因子回测

策略：`strategy/strategies/factor_single/`（每实例 `params.factor` 只启用一个因子）。

```bash
# 单个
python run_backtest.py --config config/backtest_factor_mid_dev_15.yaml

# 矩阵回测（多品种 × 多年份）
python tools/run_factor_backtests.py --products A,AU,OI --years 2024,2025

# PnL 毛利 vs 手续费拆解
python tools/analyze_factor_pnl.py --glob "factor_*"
```

对比表：`output/factor_top5_comparison.csv`（单品种单年）或 `output/factor_comparison_matrix.csv`

| 策略 id | 因子 | 默认持有期 |
|---------|------|-----------|
| factor_z_close_15 | z_close（公平对照） | 15 min |
| factor_mid_dev_15 | mid_dev | 15 min |
| factor_pct_rank_15 | pct_rank | 15 min |
| factor_mom_5 | mom_5 | 5 min |
| factor_carry_ann_60 | carry_ann | 60 min |
| factor_breakout_down_15 | breakout_down_60 | 15 min |
| factor_combo_mid_pct_15 | mid_dev 门控 + pct_rank | 15 min |
| example_dom_sub | z_close (框架 baseline) | — |
