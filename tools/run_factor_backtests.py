"""批量跑因子回测：支持多品种 × 多年份矩阵对比。"""
from __future__ import annotations

import argparse
import copy
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent

STRATEGY_CONFIGS = [
    ("baseline_dom_sub", "config/backtest_factor_baseline_dom_sub.yaml", "example_dom_sub"),
    ("z_close@15", "config/backtest_factor_z_close_15.yaml", "factor_z_close_15"),
    ("mid_dev@15", "config/backtest_factor_mid_dev_15.yaml", "factor_mid_dev_15"),
    ("pct_rank@15", "config/backtest_factor_pct_rank_15.yaml", "factor_pct_rank_15"),
    ("carry_ann@60", "config/backtest_factor_carry_ann_60.yaml", "factor_carry_ann_60"),
    ("breakout_down@15", "config/backtest_factor_breakout_down_15.yaml", "factor_breakout_down_15"),
    ("combo_mid_pct@15", "config/backtest_factor_combo_mid_pct_15.yaml", "factor_combo_mid_pct_15"),
    ("oi_mid_dev@15", "config/backtest_factor_oi_mid_dev_15.yaml", "factor_oi_mid_dev_15"),
    ("oi_combo@15", "config/backtest_factor_oi_combo_15.yaml", "factor_oi_combo_15"),
]

UNTESTED_STRATEGY_CONFIGS = [
    ("depth_imb@15", "config/backtest_factor_depth_imb_15.yaml", "factor_depth_imb_15"),
    ("range_pct@15", "config/backtest_factor_range_pct_15.yaml", "factor_range_pct_15"),
    ("vol_ratio@15", "config/backtest_factor_vol_ratio_15.yaml", "factor_vol_ratio_15"),
    ("breakout_up@15", "config/backtest_factor_breakout_up_15.yaml", "factor_breakout_up_15"),
    ("boll_pct_b@15", "config/backtest_factor_boll_pct_b_15.yaml", "factor_boll_pct_b_15"),
    ("z_bid@15", "config/backtest_factor_z_bid_15.yaml", "factor_z_bid_15"),
    ("z_ask@15", "config/backtest_factor_z_ask_15.yaml", "factor_z_ask_15"),
    ("quote_width@15", "config/backtest_factor_quote_width_15.yaml", "factor_quote_width_15"),
    ("realized_vol_20@15", "config/backtest_factor_realized_vol_20_15.yaml", "factor_realized_vol_20_15"),
    ("realized_vol_120@60", "config/backtest_factor_realized_vol_120_60.yaml", "factor_realized_vol_120_60"),
    ("seasonal_dev@15", "config/backtest_factor_seasonal_dev_15.yaml", "factor_seasonal_dev_15"),
    ("mom_15@15", "config/backtest_factor_mom_15_15.yaml", "factor_mom_15_15"),
    ("mom_60@60", "config/backtest_factor_mom_60_60.yaml", "factor_mom_60_60"),
    ("leg_mom_diff@15", "config/backtest_factor_leg_mom_diff_15.yaml", "factor_leg_mom_diff_15"),
    ("eff_spread@15", "config/backtest_factor_eff_spread_15.yaml", "factor_eff_spread_15"),
]


def _make_config(
    base_path: Path,
    product: str,
    year: str,
    strategy_key: str,
    tmp_dir: Path,
) -> tuple[Path, str]:
    with base_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = copy.deepcopy(cfg)
    strategy = cfg.get("strategy", strategy_key)
    run_id = f"{strategy}_{product}_{year}"
    cfg["run_id"] = run_id
    cfg["strategy"] = strategy
    if "universe" not in cfg:
        cfg["universe"] = {}
    cfg["universe"]["products"] = [product]
    if "data" not in cfg:
        cfg["data"] = {}
    cfg["data"]["years"] = [year]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / f"{run_id}.yaml"
    with out_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return out_path, run_id


def _read_summary(run_id: str, output_dir: Path) -> dict | None:
    summary_path = output_dir / run_id / "performance" / "summary.csv"
    if not summary_path.is_file():
        return None
    s = pd.read_csv(summary_path, index_col=0)
    row = s.iloc[0]
    return {
        "run_id": run_id,
        "total_return": float(row.get("累计收益率", 0)),
        "sharpe": float(row.get("夏普比率", 0)),
        "max_dd": float(row.get("最大回撤", 0)),
        "trades": float(row.get("成交笔数", 0)),
        "commission": float(row.get("累计手续费", 0)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="批量因子回测矩阵")
    ap.add_argument("--products", default="A", help="逗号分隔品种，如 A,AU,OI")
    ap.add_argument("--years", default="2024", help="逗号分隔年份，如 2024,2025")
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--skip-run", action="store_true", help="仅汇总已有结果")
    ap.add_argument(
        "--set",
        choices=["default", "untested", "all"],
        default="default",
        help="策略集：default=已测, untested=未测因子, all=全部",
    )
    args = ap.parse_args()

    if args.set == "untested":
        configs = UNTESTED_STRATEGY_CONFIGS
    elif args.set == "all":
        configs = STRATEGY_CONFIGS + UNTESTED_STRATEGY_CONFIGS
    else:
        configs = STRATEGY_CONFIGS

    products = [p.strip().upper() for p in args.products.split(",") if p.strip()]
    years = [y.strip() for y in args.years.split(",") if y.strip()]
    out_root = ROOT / args.output_dir
    tmp_dir = ROOT / "config" / "_generated"

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)

    rows = []
    for label, cfg_rel, strategy_key in configs:
        base_path = ROOT / cfg_rel
        if not base_path.is_file():
            print(f"[skip] missing {cfg_rel}")
            continue
        for product in products:
            for year in years:
                cfg_path, run_id = _make_config(base_path, product, year, strategy_key, tmp_dir)
                print(f"\n=== {label} | {product} | {year} => {run_id} ===")
                t0 = time.time()
                if not args.skip_run:
                    r = subprocess.run(
                        [
                            str(py), str(ROOT / "run_backtest.py"),
                            "--config", str(cfg_path.relative_to(ROOT)),
                            "--config-dir", "config",
                        ],
                        cwd=str(ROOT),
                    )
                    if r.returncode != 0:
                        print(f"[FAIL] {cfg_path}")
                        continue
                stats = _read_summary(run_id, out_root)
                if stats is None:
                    print(f"[skip] no summary for {run_id}")
                    continue
                rows.append({
                    "label": label,
                    "product": product,
                    "year": year,
                    "sec": round(time.time() - t0, 1),
                    **stats,
                })

    if not rows:
        print("no results")
        return

    df = pd.DataFrame(rows)
    print("\n" + "=" * 80)
    print(df.to_string(index=False))

    matrix_name = "factor_comparison_matrix.csv"
    if args.set == "untested":
        matrix_name = "untested_factor_matrix.csv"
    matrix_path = out_root / matrix_name
    df.to_csv(matrix_path, index=False)
    print(f"\n[ok] saved {matrix_path}")

    # 兼容旧版单表
    if len(products) == 1 and len(years) == 1:
        legacy = df.rename(columns={"total_return": "total_return"})[
            ["label", "run_id", "total_return", "sharpe", "max_dd", "trades", "sec"]
        ]
        legacy_path = out_root / "factor_top5_comparison.csv"
        legacy.to_csv(legacy_path, index=False)
        print(f"[ok] saved {legacy_path}")


if __name__ == "__main__":
    main()
