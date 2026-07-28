"""backtest/engine/context.py — 回测策略上下文。"""
from __future__ import annotations

from backtest.data.market_store import InMemoryMarketStore
from backtest.portfolio.position_book import BacktestPositionBook, StrategyPos
from utils.logger import get_logger


class BacktestStrategyContext:
    def __init__(
        self, strategy_id: str, market: InMemoryMarketStore,
        book: BacktestPositionBook, symbol_exchange: dict[str, str],
        symbol_price_tick: dict[str, float] | None = None,
    ) -> None:
        self.strategy_id = strategy_id
        self._market = market
        self._book = book
        self._symbol_exchange = symbol_exchange
        self._symbol_price_tick: dict[str, float] = symbol_price_tick or {}
        self._logger = get_logger(f"Strategy.{strategy_id}")

    def get_bars(self, symbol: str, source: str = "comb",
                 interval: str = "1m", limit: int = 240, exchange: str | None = None) -> list[dict]:
        ex = exchange if exchange is not None else self._symbol_exchange.get(symbol.strip().lower(), "")
        return self._market.query_bars(symbol, ex, interval, source, limit)

    def get_position(self, symbol: str) -> StrategyPos:
        return self._book.get_strategy_position(self.strategy_id, symbol)

    def exchange_of(self, symbol: str) -> str:
        return self._symbol_exchange.get(symbol.strip().lower(), "")

    def price_tick_of(self, symbol: str) -> float:
        return self._symbol_price_tick.get(symbol.strip().lower(), 0.0)

    def register_symbol(self, symbol: str, exchange: str, price_tick: float) -> None:
        key = symbol.strip().lower()
        self._symbol_exchange[key] = exchange
        self._symbol_price_tick[key] = float(price_tick)

    def log(self, msg: str) -> None:
        self._logger.info(msg)

    def log_warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def log_debug(self, msg: str) -> None:
        self._logger.debug(msg)

    # 回测无 tick；保留接口兼容
    def get_tick(self, symbol: str):
        return None
