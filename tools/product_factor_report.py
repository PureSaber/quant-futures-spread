"""品种-因子匹配排名报告。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FACTOR_LABEL_MAP = {
    "factor_mid_dev_15": "mid_dev",
    "factor_pct_rank_15": "pct_rank",
    "factor_combo_mid_pct_15": "combo_mid_pct",
    "factor_oi_mid_dev_15": "mid_dev",
    "factor_oi_combo_15": "combo_mid_pct",
    "factor_carry_ann_60": "carry_ann",
    "factor_breakout_down_15": "breakout_down_60",
    "factor_breakout_up_15": "breakout_up_60",
    "factor_z_close_15": "z_close",
    "factor_depth_imb_15": "depth_imb",
    "factor_range_pct_15": "range_pct",
    "factor_vol_ratio_15": "vol_ratio",
    "factor_boll_pct_b_15": "boll_pct_b",
    "factor_z_bid_15": "z_bid",
    "factor_z_ask_15": "z_ask",
    "factor_quote_width_15": "quote_width",
    "factor_realized_vol_20_15": "realized_vol_20",
    "factor_realized_vol_120_60": "realized_vol_120",
    "factor_seasonal_dev_15": "seasonal_dev",
    "factor_mom_15_15": "mom_15",
    "factor_mom_60_60": "mom_60",
    "factor_leg_mom_diff_15": "leg_mom_diff_15",
    "factor_eff_spread_15": "eff_spread",
    "example_dom_sub": "z_close",
}


def _load_sector_ic(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    ic = pd.read_csv(path)
    ic["abs_ic"] = ic["rank_ic"].abs()
    return ic.sort_values(["sector", "abs_ic"], ascending=[True, False])


def _product_sector(product: str, future_list: Path) -> str:
    if not future_list.is_file():
        return "Unknown"
    fl = pd.read_csv(future_list)
    row = fl[fl["product"].str.upper() == product.upper()]
    if row.empty:
        return "Unknown"
    return str(row.iloc[0].get("industrySub") or row.iloc[0].get("bk") or "Unknown")


def _factor_from_run(run_id: str, label: str) -> str:
    for prefix, fac in FACTOR_LABEL_MAP.items():
        if run_id.startswith(prefix) or prefix in run_id:
            return fac
    if "mid_dev" in label:
        return "mid_dev"
    if "combo" in label:
        return "combo_mid_pct"
    if "pct_rank" in label:
        return "pct_rank"
    return label.split("@")[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="品种-因子匹配排名")
    ap.add_argument("--matrix", default="output/factor_comparison_matrix.csv")
    ap.add_argument("--pnl", default="output/factor_pnl_decomposition.csv")
    ap.add_argument("--ic-sector", default="research/output/round_1/ic_by_sector.csv")
    ap.add_argument("--future-list", default="config/future_list.csv")
    ap.add_argument("--out", default="output/product_factor_ranking.csv")
    args = ap.parse_args()

    matrix = pd.read_csv(ROOT / args.matrix)
    pnl_path = ROOT / args.pnl
    pnl = pd.read_csv(pnl_path) if pnl_path.is_file() else pd.DataFrame()
    ic_sector = _load_sector_ic(ROOT / args.ic_sector)
    fl = ROOT / args.future_list

    rows = []
    for (product, year), g in matrix.groupby(["product", "year"]):
        sector = _product_sector(str(product), fl)
        baseline = g[g["label"].str.contains("baseline", case=False)]
        baseline_ret = float(baseline["total_return"].iloc[0]) if not baseline.empty else 0.0

        for _, r in g.iterrows():
            if "baseline" in str(r["label"]):
                continue
            fac = _factor_from_run(str(r["run_id"]), str(r["label"]))
            ic_val = None
            if not ic_sector.empty:
                sub = ic_sector[
                    (ic_sector["sector"] == sector) & (ic_sector["factor"] == fac) & (ic_sector["horizon"] == 15)
                ]
                if not sub.empty:
                    ic_val = float(sub.iloc[0]["rank_ic"])

            gross = net = None
            if not pnl.empty and "run_id" in pnl.columns:
                p = pnl[pnl["run_id"] == r["run_id"]]
                if not p.empty:
                    gross = float(p.iloc[0].get("gross_return_pct", 0))
                    net = float(p.iloc[0].get("net_return_pct", 0))

            excess = float(r["total_return"]) - baseline_ret
            rows.append({
                "product": product,
                "year": year,
                "sector": sector,
                "label": r["label"],
                "factor": fac,
                "run_id": r["run_id"],
                "net_return": float(r["total_return"]),
                "excess_vs_baseline": excess,
                "sharpe": float(r["sharpe"]),
                "trades": float(r["trades"]),
                "commission": float(r.get("commission", 0)),
                "ic_15": ic_val,
                "gross_return_pct": gross,
                "net_return_pct": net,
            })

    if not rows:
        print("no data")
        return

    df = pd.DataFrame(rows)
    df["score"] = (
        df["net_return"].fillna(0) * 0.5
        + df["excess_vs_baseline"].fillna(0) * 0.3
        + df["ic_15"].fillna(0).abs() * 0.2
    )
    df = df.sort_values(["product", "year", "score"], ascending=[True, True, False])
    df["rank_in_product"] = df.groupby(["product", "year"]).cumcount() + 1

    out_path = ROOT / args.out
    df.to_csv(out_path, index=False)
    print(df.head(20).to_string(index=False))
    print(f"\n[ok] saved {out_path}")


if __name__ == "__main__":
    main()
