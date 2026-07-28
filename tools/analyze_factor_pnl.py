"""拆解因子回测毛利 vs 手续费。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COL_MAP = {
    "实例ID": "instance_id",
    "价差合约": "symbol",
    "成交时间": "datetime",
    "交易日": "trading_day",
    "方向": "direction",
    "开平": "offset",
    "成交价": "price",
    "成交量": "volume",
    "手续费": "commission",
}


def _load_future_points(future_list: Path) -> dict[str, float]:
    if not future_list.is_file():
        return {}
    df = pd.read_csv(future_list)
    if "product" not in df.columns or "point" not in df.columns:
        return {}
    return {str(r["product"]).upper(): float(r["point"]) for _, r in df.iterrows()}


def _product_of(symbol: str) -> str:
    return symbol.split("&")[0].strip().upper()[:2] if "&" in symbol else symbol[:2].upper()


def analyze_trades(trades_path: Path, capital: float, point_map: dict[str, float]) -> dict:
    df = pd.read_csv(trades_path)
    df = df.rename(columns={k: v for k, v in COL_MAP.items() if k in df.columns})
    if df.empty:
        return {}

    total_comm = float(df["commission"].sum())
    rounds: list[dict] = []
    open_row = None
    for _, row in df.iterrows():
        offset = str(row.get("offset", "")).upper()
        if offset == "OPEN":
            open_row = row
        elif offset == "CLOSE" and open_row is not None:
            sym = str(row["symbol"])
            prod = _product_of(sym)
            point = point_map.get(prod, 10.0)
            vol = float(row["volume"])
            open_px = float(open_row["price"])
            close_px = float(row["price"])
            direction = str(open_row["direction"]).upper()
            if direction == "LONG":
                gross = (close_px - open_px) * point * vol
            else:
                gross = (open_px - close_px) * point * vol
            comm = float(open_row["commission"]) + float(row["commission"])
            rounds.append({
                "symbol": sym,
                "direction": direction,
                "gross_pnl": gross,
                "commission": comm,
                "net_pnl": gross - comm,
                "open_time": open_row["datetime"],
                "close_time": row["datetime"],
            })
            open_row = None

    if not rounds:
        return {
            "rounds": 0,
            "total_commission": total_comm,
            "gross_pnl": 0.0,
            "net_pnl": -total_comm,
        }

    rdf = pd.DataFrame(rounds)
    gross = float(rdf["gross_pnl"].sum())
    net = float(rdf["net_pnl"].sum())
    wins = int((rdf["net_pnl"] > 0).sum())
    hold_min = (
        pd.to_datetime(rdf["close_time"]) - pd.to_datetime(rdf["open_time"])
    ).dt.total_seconds() / 60.0

    by_sym = rdf.groupby("symbol").agg(
        rounds=("net_pnl", "count"),
        gross_pnl=("gross_pnl", "sum"),
        net_pnl=("net_pnl", "sum"),
    ).reset_index()

    return {
        "rounds": len(rdf),
        "win_rate": wins / len(rdf) if len(rdf) else 0.0,
        "avg_hold_min": float(hold_min.mean()) if len(hold_min) else 0.0,
        "gross_pnl": gross,
        "total_commission": total_comm,
        "net_pnl": net,
        "gross_return": gross / capital if capital else 0.0,
        "net_return": net / capital if capital else 0.0,
        "by_symbol": by_sym,
        "rounds_df": rdf,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="拆解因子回测 PnL")
    ap.add_argument("--run-id", action="append", default=[], help="回测 run_id，可重复")
    ap.add_argument("--output-dir", default="output", help="回测输出根目录")
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--future-list", default="config/future_list.csv")
    ap.add_argument("--glob", default="factor_*", help="run_id 通配（未指定 --run-id 时）")
    args = ap.parse_args()

    out_root = ROOT / args.output_dir
    point_map = _load_future_points(ROOT / args.future_list)
    run_ids = list(args.run_id)
    if not run_ids:
        run_ids = sorted(p.name for p in out_root.glob(args.glob) if p.is_dir())

    rows = []
    for run_id in run_ids:
        trades_path = out_root / run_id / "trades" / "trades.csv"
        if not trades_path.is_file():
            print(f"[skip] no trades: {run_id}")
            continue
        stats = analyze_trades(trades_path, args.capital, point_map)
        if not stats:
            continue
        rows.append({
            "run_id": run_id,
            "rounds": stats["rounds"],
            "win_rate": round(stats["win_rate"], 4),
            "avg_hold_min": round(stats["avg_hold_min"], 2),
            "gross_pnl": round(stats["gross_pnl"], 2),
            "commission": round(stats["total_commission"], 2),
            "net_pnl": round(stats["net_pnl"], 2),
            "gross_return_pct": round(stats["gross_return"] * 100, 4),
            "net_return_pct": round(stats["net_return"] * 100, 4),
            "cost_drag_pct": round(stats["total_commission"] / args.capital * 100, 4),
        })
        detail_dir = out_root / run_id / "analysis"
        detail_dir.mkdir(parents=True, exist_ok=True)
        if "by_symbol" in stats:
            stats["by_symbol"].to_csv(detail_dir / "pnl_by_symbol.csv", index=False)
        if "rounds_df" in stats:
            stats["rounds_df"].to_csv(detail_dir / "round_trips.csv", index=False)

    if not rows:
        print("no results")
        return
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    out_csv = out_root / "factor_pnl_decomposition.csv"
    df.to_csv(out_csv, index=False)
    print(f"\n[ok] saved {out_csv}")


if __name__ == "__main__":
    main()
