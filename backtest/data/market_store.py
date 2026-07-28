"""backtest/data/market_store.py — 内存 K 线库。"""
from __future__ import annotations

from collections import defaultdict, deque

from backtest.types import BarData


class InMemoryMarketStore:
    def __init__(self, max_bars_per_key: int = 5000) -> None:
        self._max = max_bars_per_key
        self._bars: dict[tuple[str, str, str, str], deque[BarData]] = defaultdict(deque)

    def _key(self, symbol: str, exchange: str, interval: str, source: str) -> tuple[str, str, str, str]:
        return (
            (symbol or "").strip().lower(),
            (exchange or "").strip().upper(),
            (interval or "1m").strip(),
            (source or "").strip(),
        )

    def append(self, bar: BarData) -> None:
        k = self._key(bar.symbol, bar.exchange, bar.interval, bar.source)
        q = self._bars[k]
        q.append(bar)
        while len(q) > self._max:
            q.popleft()

    def query_bars(
        self, symbol: str, exchange: str, interval: str = "1m",
        source: str = "", limit: int = 240,
    ) -> list[dict]:
        k = self._key(symbol, exchange, interval, source)
        rows = list(self._bars.get(k, ()))
        tail = rows[-int(limit):] if limit else rows
        return [
            {
                "symbol": b.symbol,
                "exchange": b.exchange,
                "interval": b.interval,
                "source": b.source,
                "datetime": b.datetime.isoformat(sep=" "),
                "product_id": b.product_id,
                "open_price": b.open_price,
                "high_price": b.high_price,
                "low_price": b.low_price,
                "close_price": b.close_price,
                "volume": b.volume,
                "open_interest": b.open_interest,
                "trading_day": b.trading_day,
                "action_day": b.action_day,
                "bid_low": b.bid_low,
                "bid_high": b.bid_high,
                "ask_low": b.ask_low,
                "ask_high": b.ask_high,
                "bid_price": b.bid_price,
                "ask_price": b.ask_price,
                "close_20": b.close_20,
                "close_50": b.close_50,
                "close_80": b.close_80,
                "leg_close_x": b.leg_close_x,
                "leg_close_y": b.leg_close_y,
            }
            for b in tail
        ]
