"""utils/calendar_peers.py — 主力表日程下的当日有效 spread（策略/回测共用）。"""
from __future__ import annotations

from typing import Dict, List, Optional


def _td_str(tradingday) -> str:
    import pandas as pd
    return str(pd.Timestamp(tradingday).normalize())[:10]


def active_spreads_on_day(
    calendar_by_product: Dict[str, Dict[str, str]],
    tradingday,
    universe: Optional[List[str]] = None,
) -> List[str]:
    """当日各 product 的 calendar spread（去重）。"""
    td = _td_str(tradingday)
    seen: set[str] = set()
    out: list[str] = []
    for day_map in calendar_by_product.values():
        sp = day_map.get(td)
        if not sp or sp in seen:
            continue
        if universe is not None and sp not in universe:
            continue
        seen.add(sp)
        out.append(sp)
    return out
