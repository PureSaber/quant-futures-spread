"""strategy/framework/context.py —— 策略只读上下文契约（API 说明）。

完整实现在：
- 回测：``core.engine.context.BacktestStrategyContext``
- 实盘：FuturesSpread 仓库 ``strategy/framework/context.py``（依赖 CTP / tick / storage）

本文件仅定义策略可依赖的方法面，避免在本仓库 import 实盘专有模块。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StrategyContext(Protocol):
    """策略 ``self.ctx`` 的最小只读接口。"""

    strategy_id: str

    def get_bars(
        self,
        symbol: str,
        source: str = "comb",
        interval: str = "1m",
        limit: int = 240,
        exchange: str | None = None,
    ) -> list[dict]: ...

    def get_position(self, symbol: str) -> Any: ...

    def exchange_of(self, symbol: str) -> str: ...

    def price_tick_of(self, symbol: str) -> float: ...

    def log(self, msg: str) -> None: ...

    def log_warning(self, msg: str) -> None: ...

    def log_debug(self, msg: str) -> None: ...


__all__ = ["StrategyContext"]
