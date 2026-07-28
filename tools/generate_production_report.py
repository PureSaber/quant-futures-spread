"""生产策略 IS/OOS 报告模板生成。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRODUCTION_STRATEGIES = [
    ("factor_mid_dev_15", "OI", "2024", "2025"),
    ("factor_combo_mid_pct_15", "OI", "2024", "2025"),
    ("factor_oi_mid_dev_15", "OI", "2024", "2025"),
    ("factor_oi_combo_15", "OI", "2024", "2025"),
]


def _read_summary(run_id: str) -> dict | None:
    p = ROOT / "output" / run_id / "performance" / "summary.csv"
    if not p.is_file():
        return None
    row = pd.read_csv(p, index_col=0).iloc[0]
    return {
        "total_return": float(row.get("累计收益率", 0)),
        "sharpe": float(row.get("夏普比率", 0)),
        "max_dd": float(row.get("最大回撤", 0)),
        "trades": float(row.get("成交笔数", 0)),
        "commission": float(row.get("累计手续费", 0)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="生产策略 IS/OOS 报告")
    ap.add_argument("--out", default="output/production_strategy_report.md")
    args = ap.parse_args()

    lines = ["# 生产策略 IS/OOS 报告", ""]
    rows = []
    for sid, product, is_year, oos_year in PRODUCTION_STRATEGIES:
        is_id = f"{sid}_{product}_{is_year}"
        oos_id = f"{sid}_{product}_{oos_year}"
        is_s = _read_summary(is_id)
        oos_s = _read_summary(oos_id)
        lines.append(f"## {sid} ({product})")
        lines.append("")
        lines.append("| 区间 | 净收益 | Sharpe | 最大回撤 | 成交笔数 | 手续费 |")
        lines.append("|------|--------|--------|----------|----------|--------|")
        for label, rid, s in [("IS", is_id, is_s), ("OOS", oos_id, oos_s)]:
            if s:
                lines.append(
                    f"| {label} {is_year if label=='IS' else oos_year} | "
                    f"{s['total_return']*100:.2f}% | {s['sharpe']:.2f} | "
                    f"{s['max_dd']*100:.2f}% | {s['trades']:.0f} | {s['commission']:.0f} |"
                )
                rows.append({"strategy": sid, "period": label, "run_id": rid, **s})
            else:
                lines.append(f"| {label} | — | — | — | — | — | (未跑: {rid})")
        lines.append("")
        lines.append(f"- 配置: `config/backtest_{sid}.yaml`")
        lines.append(f"- OOS 配置: `config/backtest_{sid}_{oos_year}.yaml`")
        lines.append("")

    out_md = ROOT / args.out
    out_md.write_text("\n".join(lines), encoding="utf-8")
    if rows:
        pd.DataFrame(rows).to_csv(ROOT / "output" / "production_strategy_summary.csv", index=False)
    print(f"[ok] {out_md}")


if __name__ == "__main__":
    main()
