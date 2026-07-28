"""OI 因子小网格搜索（IS=2024，OOS=2025 硬验证）。"""
from __future__ import annotations

import argparse
import copy
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent

GRID = {
    "entry_low": [0.05, 0.1],
    "entry_high": [0.9, 0.95],
    "min_hold_bars": [10, 15],
    "horizon_min": [15, 20],
}

STRATEGIES = [
    ("mid_dev", "factor_mid_dev_15", "config/backtest_factor_mid_dev_15.yaml"),
    ("combo", "factor_combo_mid_pct_15", "config/backtest_factor_combo_mid_pct_15.yaml"),
]


def _run_one(py: Path, cfg_path: Path) -> dict | None:
    r = subprocess.run(
        [str(py), str(ROOT / "run_backtest.py"), "--config", str(cfg_path.relative_to(ROOT)), "--config-dir", "config"],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        return None
    with cfg_path.open(encoding="utf-8") as f:
        run_id = yaml.safe_load(f).get("run_id")
    summary = ROOT / "output" / run_id / "performance" / "summary.csv"
    if not summary.is_file():
        return None
    row = pd.read_csv(summary, index_col=0).iloc[0]
    return {
        "run_id": run_id,
        "total_return": float(row.get("累计收益率", 0)),
        "sharpe": float(row.get("夏普比率", 0)),
        "trades": float(row.get("成交笔数", 0)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="OI 因子网格搜索")
    ap.add_argument("--product", default="OI")
    ap.add_argument("--is-year", default="2024")
    ap.add_argument("--oos-year", default="2025")
    ap.add_argument("--phase", choices=["is", "oos", "all"], default="is")
    args = ap.parse_args()

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)
    tmp_dir = ROOT / "config" / "_generated" / "grid"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    is_rows = []
    if args.phase in ("is", "all"):
        for fac_label, strategy_id, base_cfg_rel in STRATEGIES:
            with (ROOT / base_cfg_rel).open(encoding="utf-8") as f:
                base = yaml.safe_load(f) or {}
            for el in GRID["entry_low"]:
                for eh in GRID["entry_high"]:
                    for mh in GRID["min_hold_bars"]:
                        for hz in GRID["horizon_min"]:
                            cfg = copy.deepcopy(base)
                            run_id = f"{strategy_id}_grid_{args.product}_{args.is_year}_el{el}_eh{eh}_mh{mh}_h{hz}"
                            cfg["run_id"] = run_id
                            cfg["strategy"] = strategy_id
                            cfg["universe"]["products"] = [args.product.upper()]
                            cfg["data"]["years"] = [args.is_year]
                            cfg["strategy_params"] = {
                                "entry_low": el,
                                "entry_high": eh,
                                "min_hold_bars": mh,
                                "horizon_min": hz,
                                "gate_low": el,
                                "gate_high": eh,
                            }
                            cfg_path = tmp_dir / f"{run_id}.yaml"
                            cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
                            print(f"[IS] {fac_label} el={el} eh={eh} mh={mh} h={hz}")
                            stats = _run_one(py, cfg_path)
                            if stats:
                                is_rows.append({
                                    "phase": "is",
                                    "factor": fac_label,
                                    "entry_low": el,
                                    "entry_high": eh,
                                    "min_hold_bars": mh,
                                    "horizon_min": hz,
                                    **stats,
                                })

    if is_rows:
        is_df = pd.DataFrame(is_rows).sort_values("total_return", ascending=False)
        is_path = ROOT / "output" / f"grid_search_is_{args.product}.csv"
        is_df.to_csv(is_path, index=False)
        print(f"\n[ok] IS results: {is_path}")
        best = is_df.iloc[0]
        print(f"Best IS: {best['factor']} return={best['total_return']:.4f} trades={best['trades']:.0f}")

        if args.phase in ("oos", "all"):
            base_rel = STRATEGIES[1][2] if best["factor"] == "combo" else STRATEGIES[0][2]
            with (ROOT / base_rel).open(encoding="utf-8") as f:
                cfg = copy.deepcopy(yaml.safe_load(f) or {})
            strategy_id = STRATEGIES[0][1] if best["factor"] == "mid_dev" else STRATEGIES[1][1]
            run_id = f"{strategy_id}_grid_best_{args.product}_{args.oos_year}"
            cfg["run_id"] = run_id
            cfg["strategy"] = strategy_id
            cfg["universe"]["products"] = [args.product.upper()]
            cfg["data"]["years"] = [args.oos_year]
            cfg["strategy_params"] = {
                "entry_low": float(best["entry_low"]),
                "entry_high": float(best["entry_high"]),
                "min_hold_bars": int(best["min_hold_bars"]),
                "horizon_min": int(best["horizon_min"]),
                "gate_low": float(best["entry_low"]),
                "gate_high": float(best["entry_high"]),
            }
            cfg_path = tmp_dir / f"{run_id}.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            print(f"\n[OOS] validating best params on {args.oos_year} ...")
            oos_stats = _run_one(py, cfg_path)
            if oos_stats:
                oos_df = pd.DataFrame([{**best.to_dict(), **oos_stats, "phase": "oos"}])
                oos_path = ROOT / "output" / f"grid_search_oos_{args.product}.csv"
                oos_df.to_csv(oos_path, index=False)
                print(f"[ok] OOS result: {oos_path} return={oos_stats['total_return']:.4f}")


if __name__ == "__main__":
    main()
