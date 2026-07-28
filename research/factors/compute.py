"""汇总六大家族因子计算。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.common.contracts import days_between_contracts, spread_months
from research.factors.registry import factor_names


def _safe_div(a, b, fill=np.nan):
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(np.abs(b) > 1e-12, a / b, fill)
    return out


def compute_factors(
    df: pd.DataFrame,
    spread_id: str,
    file_year: int,
    lookback: int = 60,
    vol_short: int = 20,
    vol_long: int = 120,
    roll_dates: set[str] | None = None,
) -> pd.DataFrame:
    out = df.copy()
    close = out["close"].astype(float)
    lb = max(lookback, 5)

    # ── A 均值回归 ──
    mu = close.rolling(lb, min_periods=lb).mean()
    std = close.rolling(lb, min_periods=lb).std(ddof=0)
    out["z_close"] = _safe_div(close - mu, std)
    out["pair_z"] = out["z_close"]

    if all(c in out.columns for c in ("close_20", "close_80")):
        denom = out["close_80"] - out["close_20"]
        out["pct_rank"] = _safe_div(close - out["close_20"], denom)
    else:
        q20 = close.rolling(lb).quantile(0.2)
        q80 = close.rolling(lb).quantile(0.8)
        out["pct_rank"] = _safe_div(close - q20, q80 - q20)

    if all(c in out.columns for c in ("bidPrice_20", "bidPrice_80")):
        bd = out["bidPrice_80"] - out["bidPrice_20"]
        out["z_bid"] = _safe_div(out["bidPrice"] - out["bidPrice_20"], bd)
    if all(c in out.columns for c in ("askPrice_20", "askPrice_80")):
        ad = out["askPrice_80"] - out["askPrice_20"]
        out["z_ask"] = _safe_div(out["askPrice"] - out["askPrice_20"], ad)

    ma = close.rolling(lb, min_periods=lb).mean()
    sd = close.rolling(lb, min_periods=lb).std(ddof=0)
    out["boll_pct_b"] = _safe_div(close - (ma - 2 * sd), 4 * sd)

    # AR(1) 半衰期代理（向量化）
    ret = close.diff()
    lag_c = close.shift(1)
    cov = ret.rolling(lb, min_periods=lb).cov(lag_c)
    var = lag_c.rolling(lb, min_periods=lb).var()
    beta = _safe_div(cov, var, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        hl_arr = np.where((beta > 0) & (beta < 1), -np.log(2) / np.log(beta), np.nan)
    out["half_life_proxy"] = hl_arr

    if "stationarity" in out.columns:
        out["coint_gate"] = out["stationarity"].astype(str).str.contains("协整", na=False).astype(float)
    else:
        out["coint_gate"] = np.nan

    # ── B 动量 ──
    for h, name in [(5, "mom_5"), (15, "mom_15"), (60, "mom_60"), (240, "mom_240")]:
        out[name] = close.diff(h)
    if "close_x" in out.columns and "close_y" in out.columns:
        out["leg_mom_diff_15"] = out["close_x"].diff(15) - out["close_y"].diff(15)
    else:
        out["leg_mom_diff_15"] = np.nan
    hi60 = close.rolling(60, min_periods=30).max()
    lo60 = close.rolling(60, min_periods=30).min()
    out["breakout_up_60"] = (close >= hi60).astype(float)
    out["breakout_down_60"] = (close <= lo60).astype(float)

    # ── C 波动 ──
    out["realized_vol_20"] = close.diff().rolling(vol_short, min_periods=vol_short).std()
    out["realized_vol_120"] = close.diff().rolling(vol_long, min_periods=vol_long).std()
    out["vol_ratio"] = _safe_div(out["realized_vol_20"], out["realized_vol_120"])
    if "high" in out.columns and "low" in out.columns:
        out["range_pct"] = _safe_div(out["high"] - out["low"], close.abs())
    else:
        out["range_pct"] = np.nan

    # ── D 微观结构 ──
    if "bidPrice" in out.columns and "askPrice" in out.columns:
        out["eff_spread"] = out["askPrice"] - out["bidPrice"]
        mid = (out["bidPrice"] + out["askPrice"]) / 2.0
        out["mid_dev"] = close - mid
    else:
        out["eff_spread"] = np.nan
        out["mid_dev"] = np.nan
    if "bidVolume" in out.columns and "askVolume" in out.columns:
        tot = out["bidVolume"] + out["askVolume"]
        out["depth_imb"] = _safe_div(out["bidVolume"], tot, 0.5) - 0.5
    else:
        out["depth_imb"] = np.nan
    if all(c in out.columns for c in ("askPrice_80", "bidPrice_20")):
        out["quote_width"] = out["askPrice_80"] - out["bidPrice_20"]
    else:
        out["quote_width"] = np.nan

    # ── E 期限结构 ──
    near_m, far_m = spread_months(spread_id, file_year)
    days = days_between_contracts(near_m, far_m)
    if days:
        out["carry_ann"] = _safe_div(close, days) * 365.0
    else:
        out["carry_ann"] = np.nan

    if roll_dates and "tradingday" in out.columns:
        out["roll_window"] = out["tradingday"].isin(roll_dates).astype(float)
    else:
        out["roll_window"] = 0.0

    if "datetime" in out.columns:
        dt = pd.to_datetime(out["datetime"])
        minute_of_day = dt.dt.hour * 60 + dt.dt.minute
        seasonal = close.groupby(minute_of_day).transform("mean")
        out["seasonal_dev"] = close - seasonal
    else:
        out["seasonal_dev"] = np.nan

    keep = ["datetime", "tradingday", "close"] + factor_names()
    label_cols = [c for c in out.columns if c.startswith("fwd_")]
    keep += label_cols
    exist = [c for c in keep if c in out.columns]
    return out[exist]
