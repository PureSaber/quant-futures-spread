"""core/panel/calendar_panel.py — 主力表日程 → panel 日截面。"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from utils.calendar_peers import active_spreads_on_day
from utils.spread_sector import board_of_product, leg0_product


def build_product_daily_close(
    calendar_by_product: Dict[str, Dict[str, str]],
    daily_frames: Dict[str, pd.Series],
) -> pd.DataFrame:
    """每个 product 一列，取当日 calendar spread 的日收盘。"""
    cols: dict[str, list] = {}
    idx_set: set[pd.Timestamp] = set()
    for prod, day_map in calendar_by_product.items():
        points: list[tuple[pd.Timestamp, float]] = []
        for td, sp in sorted(day_map.items()):
            ser = daily_frames.get(sp)
            if ser is None or ser.empty:
                continue
            day_ts = pd.Timestamp(td).normalize()
            if day_ts in ser.index:
                val = ser.loc[day_ts]
            else:
                matched = ser[ser.index.normalize() == day_ts]
                val = matched.iloc[-1] if not matched.empty else np.nan
            if pd.notna(val):
                points.append((day_ts, float(val)))
        if points:
            idx, vals = zip(*points)
            cols[prod] = pd.Series(vals, index=pd.DatetimeIndex(idx))
            idx_set.update(cols[prod].index)
    if not cols:
        return pd.DataFrame()
    out = pd.DataFrame(cols).sort_index()
    return out.reindex(sorted(idx_set))


def calendar_board_peers(
    spread_symbol: str,
    tradingday: str,
    calendar_by_product: Dict[str, Dict[str, str]],
    minute_by_spread: Dict[str, pd.DataFrame],
    industry_map: Dict[str, str],
    sector_map: Dict[str, str],
    spread_to_sector: Dict[str, str],
) -> List[str]:
    """同板块/行业、且当日 calendar 有效的 peer spread。"""
    sym = str(spread_symbol)
    td = str(pd.Timestamp(tradingday).normalize())[:10]
    my_prod = leg0_product(sym)
    if industry_map:
        my_board = board_of_product(my_prod, industry_map)
        if not my_board:
            return [sym]
        out: list[str] = []
        for prod in calendar_by_product:
            if board_of_product(prod, industry_map) != my_board:
                continue
            sp = calendar_by_product[prod].get(td)
            if sp and sp in minute_by_spread:
                out.append(sp)
        return out if out else [sym]
    my_sec = spread_to_sector.get(sym) or sector_map.get(my_prod, "其他")
    out = []
    for prod in calendar_by_product:
        if sector_map.get(prod, "其他") != my_sec:
            continue
        sp = calendar_by_product[prod].get(td)
        if sp and sp in minute_by_spread:
            out.append(sp)
    return out if out else [sym]


__all__ = [
    "active_spreads_on_day",
    "build_product_daily_close",
    "calendar_board_peers",
]
