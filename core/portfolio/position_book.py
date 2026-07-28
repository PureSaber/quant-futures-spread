"""core/portfolio/position_book.py — 策略归属持仓。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StrategyPos:
    strategy_id: str
    symbol: str
    exchange: str = ""
    long_qty: float = 0.0
    short_qty: float = 0.0
    long_avg: float = 0.0
    short_avg: float = 0.0
    updated_at: datetime = field(default_factory=datetime.now)


def apply_fill_to_strategy_pos(
    pos: StrategyPos, direction: str, offset: str, vol: float, price: float
) -> None:
    direction = (direction or "").strip().upper()
    offset = (offset or "").strip().upper()
    vol = float(vol or 0)
    price = float(price or 0)
    if vol <= 0:
        return
    if offset == "OPEN":
        if direction == "LONG":
            total = pos.long_qty + vol
            pos.long_avg = (pos.long_avg * pos.long_qty + price * vol) / total if total else price
            pos.long_qty = total
        else:
            total = pos.short_qty + vol
            pos.short_avg = (pos.short_avg * pos.short_qty + price * vol) / total if total else price
            pos.short_qty = total
    else:
        if direction == "SHORT":
            pos.long_qty = max(0.0, pos.long_qty - vol)
            if pos.long_qty <= 1e-9:
                pos.long_avg = 0.0
        else:
            pos.short_qty = max(0.0, pos.short_qty - vol)
            if pos.short_qty <= 1e-9:
                pos.short_avg = 0.0
    pos.updated_at = datetime.now()


class BacktestPositionBook:
    def __init__(self) -> None:
        self._pos: dict[tuple[str, str], StrategyPos] = {}

    def get_strategy_position(self, strategy_id: str, symbol: str) -> StrategyPos:
        key = (strategy_id, (symbol or "").strip().lower())
        if key not in self._pos:
            self._pos[key] = StrategyPos(strategy_id=strategy_id, symbol=symbol)
        return self._pos[key]

    def apply_trade(self, strategy_id: str, symbol: str, direction: str,
                    offset: str, vol: float, price: float) -> StrategyPos:
        pos = self.get_strategy_position(strategy_id, symbol)
        apply_fill_to_strategy_pos(pos, direction, offset, vol, price)
        return pos
