"""backtest/data/bar_converter.py — CSV spread → BarData。"""
from __future__ import annotations

import pandas as pd

from backtest.types import BarData


def _col(df: pd.DataFrame, name: str, fallback: str | None = None) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    if fallback and fallback in df.columns:
        return pd.to_numeric(df[fallback], errors="coerce")
    raise KeyError(f"CSV 缺少列 {name!r}" + (f" 或 {fallback!r}" if fallback else ""))


def df_to_bars(
    df: pd.DataFrame, symbol: str, exchange: str, product_id: str = "",
    source: str = "comb",
) -> list[BarData]:
    if df.empty:
        return []
    out = df.copy()
    if "datetime" in out.columns:
        out["_dt"] = pd.to_datetime(out["datetime"])
    elif "tradingday" in out.columns:
        out["_dt"] = pd.to_datetime(out["tradingday"])
    else:
        raise ValueError("CSV 缺少 datetime / tradingday 列")

    close_s = _col(out, "close")
    open_s = _col(out, "open", "close") if "open" in out.columns else close_s
    high_s = _col(out, "high", "close") if "high" in out.columns else close_s
    low_s = _col(out, "low", "close") if "low" in out.columns else close_s
    vol_s = pd.to_numeric(out["volume"], errors="coerce").fillna(0) if "volume" in out.columns \
        else pd.Series(0.0, index=out.index)
    if "oi_x" in out.columns:
        oi_s = pd.to_numeric(out["oi_x"], errors="coerce").fillna(0)
    elif "oi" in out.columns:
        oi_s = pd.to_numeric(out["oi"], errors="coerce").fillna(0)
    elif "oi_y" in out.columns:
        oi_s = pd.to_numeric(out["oi_y"], errors="coerce").fillna(0)
    else:
        oi_s = pd.Series(0.0, index=out.index)
    if "tradingday" in out.columns:
        tday_s = pd.to_datetime(out["tradingday"]).dt.strftime("%Y-%m-%d")
    else:
        tday_s = out["_dt"].dt.strftime("%Y-%m-%d")
    tradable_s = out["trade"].astype(bool) if "trade" in out.columns else pd.Series(True, index=out.index)

    def _opt_col(df: pd.DataFrame, name: str, fallback: pd.Series) -> pd.Series:
        if name in df.columns:
            s = pd.to_numeric(df[name], errors="coerce")
            return s.fillna(fallback)
        return fallback

    bid_high_s = _opt_col(out, "bidPrice_high", high_s)
    bid_low_s = _opt_col(out, "bidPrice_low", low_s)
    ask_high_s = _opt_col(out, "askPrice_high", high_s)
    ask_low_s = _opt_col(out, "askPrice_low", low_s)
    bid_price_s = _opt_col(out, "bidPrice", bid_low_s)
    ask_price_s = _opt_col(out, "askPrice", ask_high_s)
    close_20_s = _opt_col(out, "close_20", pd.Series(0.0, index=out.index))
    close_50_s = _opt_col(out, "close_50", pd.Series(0.0, index=out.index))
    close_80_s = _opt_col(out, "close_80", pd.Series(0.0, index=out.index))
    leg_x_s = _opt_col(out, "close_x", pd.Series(0.0, index=out.index)) if "close_x" in out.columns \
        else pd.Series(0.0, index=out.index)
    leg_y_s = _opt_col(out, "close_y", pd.Series(0.0, index=out.index)) if "close_y" in out.columns \
        else pd.Series(0.0, index=out.index)

    pid = product_id or (symbol.split("&")[0] if "&" in symbol else symbol)[:2].upper()
    bars: list[BarData] = []
    for idx in range(len(out)):
        dt = out["_dt"].iloc[idx]
        if isinstance(dt, pd.Timestamp):
            dt = dt.to_pydatetime()
        bars.append(BarData(
            symbol=symbol,
            exchange=exchange,
            datetime=dt,
            product_id=pid,
            open_price=float(open_s.iloc[idx]),
            high_price=float(high_s.iloc[idx]),
            low_price=float(low_s.iloc[idx]),
            close_price=float(close_s.iloc[idx]),
            volume=float(vol_s.iloc[idx]),
            open_interest=float(oi_s.iloc[idx]),
            trading_day=str(tday_s.iloc[idx]),
            source=source,
            is_completed=True,
            tradable=bool(tradable_s.iloc[idx]),
            bid_high=float(bid_high_s.iloc[idx]),
            bid_low=float(bid_low_s.iloc[idx]),
            ask_high=float(ask_high_s.iloc[idx]),
            ask_low=float(ask_low_s.iloc[idx]),
            bid_price=float(bid_price_s.iloc[idx]),
            ask_price=float(ask_price_s.iloc[idx]),
            close_20=float(close_20_s.iloc[idx]),
            close_50=float(close_50_s.iloc[idx]),
            close_80=float(close_80_s.iloc[idx]),
            leg_close_x=float(leg_x_s.iloc[idx]),
            leg_close_y=float(leg_y_s.iloc[idx]),
        ))
    return bars
