"""多品种 pooled 回测（单 run 多 product 实例）。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIGS = [
    ("oi_mid_dev pooled", "config/backtest_pooled_oi_mid_dev_2024.yaml"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Pooled 多品种回测")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--aggregate", action="store_true", help="汇总各品种子 run 收益")
    args = ap.parse_args()

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)

    configs = args.config or [c for _, c in DEFAULT_CONFIGS]
    rows = []
    for cfg_rel in configs:
        cfg_path = ROOT / cfg_rel
        with cfg_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        run_id = raw["run_id"]
        products = raw.get("universe", {}).get("products", [])
        years = raw.get("data", {}).get("years", [])
        print(f"\n=== pooled run: {run_id} products={products} years={years} ===")
        r = subprocess.run(
            [str(py), str(ROOT / "run_backtest.py"), "--config", str(cfg_path.relative_to(ROOT)), "--config-dir", "config"],
            cwd=str(ROOT),
        )
        if r.returncode != 0:
            print(f"[FAIL] {cfg_rel}")
            continue
        summary = ROOT / "output" / run_id / "performance" / "summary.csv"
        if summary.is_file():
            row = pd.read_csv(summary, index_col=0).iloc[0]
            rows.append({
                "run_id": run_id,
                "products": ",".join(products),
                "years": ",".join(years),
                "total_return": float(row.get("累计收益率", 0)),
                "sharpe": float(row.get("夏普比率", 0)),
                "trades": float(row.get("成交笔数", 0)),
            })

        if args.aggregate and products and years:
            for prod in products:
                for year in years:
                    sid = raw.get("strategy", "")
                    sub_id = f"{sid}_{prod}_{year}"
                    sub_summary = ROOT / "output" / sub_id / "performance" / "summary.csv"
                    if sub_summary.is_file():
                        srow = pd.read_csv(sub_summary, index_col=0).iloc[0]
                        rows.append({
                            "run_id": sub_id,
                            "products": prod,
                            "years": year,
                            "total_return": float(srow.get("累计收益率", 0)),
                            "sharpe": float(srow.get("夏普比率", 0)),
                            "trades": float(srow.get("成交笔数", 0)),
                        })

    if rows:
        df = pd.DataFrame(rows)
        out = ROOT / "output" / "pooled_backtest_summary.csv"
        df.to_csv(out, index=False)
        print("\n" + df.to_string(index=False))
        print(f"\n[ok] saved {out}")


if __name__ == "__main__":
    main()
