"""CSV spread 加载与基础过滤。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


_NUM_COLS = [
    "open", "high", "low", "close", "close_x", "close_y",
    "bidPrice", "bidVolume", "askPrice", "askVolume",
    "close_80", "close_50", "close_20",
    "bidPrice_80", "bidPrice_50", "bidPrice_20",
    "askPrice_80", "askPrice_50", "askPrice_20",
]


def load_spread_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
    for c in _NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "trade" in df.columns:
        df["trade"] = df["trade"].astype(bool)
    else:
        df["trade"] = True
    if "tradingday" in df.columns:
        df["tradingday"] = pd.to_datetime(df["tradingday"]).dt.strftime("%Y-%m-%d")
    return df.sort_values("datetime").reset_index(drop=True)


def apply_filters(df: pd.DataFrame, require_trade: bool, max_eff_spread: float) -> pd.DataFrame:
    out = df.copy()
    if require_trade and "trade" in out.columns:
        out = out[out["trade"]]
    if "bidPrice" in out.columns and "askPrice" in out.columns:
        eff = out["askPrice"] - out["bidPrice"]
        out = out[(eff.isna()) | (eff <= max_eff_spread)]
    return out.reset_index(drop=True)


def add_forward_labels(df: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    out = df.copy()
    close = out["close"].astype(float)
    for h in horizons:
        out[f"fwd_ret_{h}"] = close.shift(-h) - close
        if "askPrice" in out.columns and "bidPrice" in out.columns:
            # 做多价差：未来 bid 出 - 当前 ask 进（保守可实现）
            out[f"fwd_realized_long_{h}"] = out["bidPrice"].shift(-h) - out["askPrice"]
    return out
