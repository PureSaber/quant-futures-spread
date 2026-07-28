"""backtest/portfolio/commission.py — 价差成交手续费（动态腿价 + 平今）。"""
from __future__ import annotations

from utils.contract_util import FutureList, product_of, split_spread
from utils.logger import get_logger
from backtest.types import BarData

logger = get_logger("Commission")


class SpreadOpenDayTracker:
    """记录各 (strategy, spread) 自空仓起算的建仓交易日，用于平今费率。"""

    def __init__(self) -> None:
        self._open_day: dict[tuple[str, str], str] = {}

    def _key(self, strategy_id: str, symbol: str) -> tuple[str, str]:
        return (strategy_id, (symbol or "").strip().lower())

    def clear_symbol(self, strategy_id: str, symbol: str) -> None:
        self._open_day.pop(self._key(strategy_id, symbol), None)

    def is_close_today(self, strategy_id: str, symbol: str, trading_day: str, offset: str) -> bool:
        if (offset or "").strip().upper() != "CLOSE":
            return False
        od = self._open_day.get(self._key(strategy_id, symbol))
        return od is not None and str(trading_day) == od

    def after_trade(
        self, strategy_id: str, symbol: str,
        net_before: float, net_after: float, trading_day: str,
    ) -> None:
        key = self._key(strategy_id, symbol)
        if abs(net_after) < 1e-9:
            self._open_day.pop(key, None)
        elif abs(net_before) < 1e-9:
            self._open_day[key] = str(trading_day)


def leg_products(symbol: str) -> tuple[str, str]:
    leg0, leg1 = split_spread(symbol)
    return product_of(leg0), product_of(leg1)


def resolve_leg_prices(
    bar: BarData, fl: FutureList, leg0_product: str, leg1_product: str,
) -> tuple[float, float]:
    x = float(getattr(bar, "leg_close_x", 0.0) or 0.0)
    y = float(getattr(bar, "leg_close_y", 0.0) or 0.0)
    if x > 0 and y > 0:
        return x, y
    if not fl.is_proportional(leg0_product) and not fl.is_proportional(leg1_product):
        return 0.0, 0.0
    raise ValueError(
        f"比例费品种 {leg0_product}/{leg1_product} 需要 close_x/close_y，"
        f"bar {bar.datetime} spread={bar.symbol} 缺失腿价"
    )


def commission_for_trade(
    fl: FutureList,
    leg0_product: str,
    leg1_product: str,
    bar: BarData,
    offset: str,
    volume: float,
    *,
    is_close_today: bool,
) -> float:
    leg0_px, leg1_px = resolve_leg_prices(bar, fl, leg0_product, leg1_product)
    per_lot = fl.commission_for_spread(
        leg0_product, leg1_product, leg0_px, leg1_px, offset,
        is_close_today=is_close_today,
    )
    return per_lot * float(volume)
