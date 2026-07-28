"""utils/spread_sector.py —— 截面 panel 纯函数辅助。"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.contract_util import product_of, split_spread


def leg0_product(spread_symbol: str) -> str:
    leg0, _ = split_spread(str(spread_symbol))
    return product_of(leg0)


def board_of_product(product: str, industry_map: Dict[str, str]) -> Optional[str]:
    return industry_map.get(str(product).upper())


def build_board_peers(
    spreads: List[str],
    industry_map: Dict[str, str],
) -> Dict[str, List[str]]:
    """spread 实例 → 同板块 peer spread 列表（仅 universe 内）。"""
    spread_board: Dict[str, Optional[str]] = {}
    for sym in spreads:
        p = leg0_product(sym)
        spread_board[sym] = board_of_product(p, industry_map)
    by_board: Dict[str, List[str]] = defaultdict(list)
    for sym, board in spread_board.items():
        if board:
            by_board[board].append(sym)
    out: Dict[str, List[str]] = {}
    for sym in spreads:
        board = spread_board.get(sym)
        out[sym] = list(by_board.get(board, [])) if board else []
    return out


def sector_ext_at(panel: Any, spread_symbol: str, tradingday) -> int:
    """查 prior-day sector extreme 标签 {-1, 0, 1}；panel 缺失时返回 0。"""
    if panel is None:
        return 0
    getter = getattr(panel, "sector_ext_for", None)
    if getter is None:
        return 0
    try:
        return int(getter(spread_symbol, tradingday))
    except Exception:
        return 0


def tradingday_i64(tradingday) -> int:
    return int(pd.Timestamp(tradingday).normalize().value)


__all__ = [
    "leg0_product",
    "board_of_product",
    "build_board_peers",
    "sector_ext_at",
    "tradingday_i64",
]
