"""backtest/portfolio/sizing.py — capital_per_layer → vol_per_layer。"""
from __future__ import annotations

import math


def capital_to_lots(capital: float, leg_close: float, multiplier: float) -> int:
    """手数 = floor(资金 / (单腿收盘价 × 合约乘数))，与 FuturesSpread 一致。"""
    if capital <= 0 or leg_close <= 0 or multiplier <= 0:
        return 0
    return int(math.floor(float(capital) / (float(leg_close) * float(multiplier))))


def compute_vol_per_layer(
    leg_close: float, multiplier: float, capital_per_layer: list[float],
) -> list[float]:
    out = []
    for cap in capital_per_layer:
        lots = capital_to_lots(float(cap), float(leg_close), float(multiplier))
        out.append(float(max(0, lots)))
    return out


def ref_leg_close_from_df(df) -> float | None:
    """取 CSV close_x 中位作为折手数参考价（近似昨收）。"""
    if df is None or df.empty or "close_x" not in df.columns:
        return None
    s = df["close_x"].astype(float)
    s = s[s > 0]
    if s.empty:
        return None
    return float(s.median())
