"""研究侧与策略侧因子值对齐测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from research.common.loader import load_spread_csv
from research.factors.compute import compute_factors
from strategies.factor_single.factors import compute_factor_value


def _rank_corr(x: pd.Series, y: pd.Series) -> float:
    if len(x) < 10:
        return float("nan")
    rx = x.rank(method="average")
    ry = y.rank(method="average")
    return float(rx.corr(ry))


def _bars_from_df(df: pd.DataFrame, end_idx: int, lookback: int = 80) -> tuple[list[dict], object]:
    start = max(0, end_idx - lookback + 1)
    sub = df.iloc[start: end_idx + 1]
    bars = []
    for _, row in sub.iterrows():
        bars.append({
            "close_price": float(row["close"]),
            "low_price": float(row.get("low", row["close"])),
            "high_price": float(row.get("high", row["close"])),
            "bid_price": float(row.get("bidPrice", row.get("bidPrice_low", row["close"]))),
            "ask_price": float(row.get("askPrice", row.get("askPrice_high", row["close"]))),
            "bid_low": float(row.get("bidPrice_low", row.get("bidPrice", row["close"]))),
            "ask_high": float(row.get("askPrice_high", row.get("askPrice", row["close"]))),
            "close_20": float(row.get("close_20", 0) or 0),
            "close_80": float(row.get("close_80", 0) or 0),
        })

    class _Bar:
        def __init__(self, row):
            self.close_price = float(row["close"])
            self.close_20 = float(row.get("close_20", 0) or 0)
            self.close_80 = float(row.get("close_80", 0) or 0)
            self.bid_price = float(row.get("bidPrice", 0) or 0)
            self.ask_price = float(row.get("askPrice", 0) or 0)

    bar = _Bar(df.iloc[end_idx])
    return bars, bar


def test_research_strategy_factor_alignment():
    csv_path = Path("D:/data/2024/A/A2405&A2409.csv")
    if not csv_path.is_file():
        return
    raw = load_spread_csv(csv_path)
    research = compute_factors(raw, "A2405&A2409", 2024, lookback=60)
    lookback = 60
    factors = ["pct_rank", "mom_5", "mid_dev", "z_close"]
    start = lookback + 10
    end = min(len(research), start + 500)
    for fac in factors:
        rs, ss = [], []
        for i in range(start, end):
            bars, bar = _bars_from_df(raw, i, lookback + 65)
            sv = compute_factor_value(fac, bars, bar, "A2405&A2409", lookback)
            rv = research.iloc[i][fac]
            if sv is None or pd.isna(rv):
                continue
            rs.append(float(rv))
            ss.append(float(sv))
        assert len(rs) >= 50, f"{fac}: too few samples"
        corr = _rank_corr(pd.Series(rs), pd.Series(ss))
        assert corr > 0.99, f"{fac} rank corr {corr:.4f} < 0.99"
