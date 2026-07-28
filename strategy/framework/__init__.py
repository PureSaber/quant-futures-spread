"""framework/ —— 策略契约层（与 FuturesSpread strategy/framework 同构）。

回测侧注入 ``BacktestStrategyContext``；实盘侧使用完整 ``StrategyContext``。
"""
from .base import (
    Strategy, TargetOrder, DirectSignal,
    OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT,
    SIDE_TO_DIRECTION,
)

__all__ = [
    "Strategy", "TargetOrder", "DirectSignal",
    "OPEN_LONG", "OPEN_SHORT", "CLOSE_LONG", "CLOSE_SHORT",
    "SIDE_TO_DIRECTION",
]
