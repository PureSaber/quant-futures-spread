"""backtest/engine/reconcile_sim.py — TargetOrder 限价撮合。"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from framework.base import (
    TargetOrder, OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT,
)
from backtest.types import BarData, TradeData

_ORDER_ID = itertools.count(1)
_TRADE_ID = itertools.count(1)


@dataclass
class _Pending:
    strategy_id: str
    symbol: str
    side: str
    price: float
    volume: float
    tag: str = ""
    order_id: str = field(default_factory=lambda: f"bt-{next(_ORDER_ID)}")


class ReconcileSimulator:
    """每根 K：策略给出完整目标集 → 更新挂单 → 按 OHLC 触达限价成交。

    同一 (strategy, symbol, side, price) 的开仓限价在未平仓前只成交一次，
    避免每根 1m bar 重复计入（网格在实盘由在途单状态保证）。
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], list[_Pending]] = {}
        self._open_filled: dict[tuple[str, str], set[tuple[str, float]]] = {}
        self._was_flat: dict[tuple[str, str], bool] = {}

    def _fill_key(self, strategy_id: str, symbol: str) -> tuple[str, str]:
        return (strategy_id, (symbol or "").strip().lower())

    def _reset_opens_if_flat(self, strategy_id: str, symbol: str, book) -> None:
        key = self._fill_key(strategy_id, symbol)
        pos = book.get_strategy_position(strategy_id, symbol)
        flat = pos.long_qty <= 1e-9 and pos.short_qty <= 1e-9
        was_flat = self._was_flat.get(key, True)
        if flat and not was_flat:
            self._open_filled.pop(key, None)
        self._was_flat[key] = flat

    def reconcile(self, strategy_id: str, symbol: str,
                  targets: list[TargetOrder], trade_mode: int = 0) -> None:
        sym = (symbol or "").strip()
        filtered = self._filter_open_targets(strategy_id, sym, targets, trade_mode)
        key = (strategy_id, sym.lower())
        self._pending[key] = [
            _Pending(strategy_id=strategy_id, symbol=sym, side=t.side,
                     price=float(t.price), volume=float(t.volume), tag=t.tag or "")
            for t in filtered
        ]

    def _filter_open_targets(self, strategy_id: str, sym: str,
                             targets: list[TargetOrder], trade_mode: int) -> list[TargetOrder]:
        if trade_mode == 0:
            return list(targets)
        out: list[TargetOrder] = []
        for t in targets:
            if t.is_open:
                if trade_mode == 1 and t.side != OPEN_LONG:
                    continue
                if trade_mode == -1 and t.side != OPEN_SHORT:
                    continue
            out.append(t)
        return out


    def reset_opens_if_flat(self, strategy_id: str, symbol: str, book) -> None:
        """持仓归零后允许同价位再次开仓（每根 bar 开始前调用）。"""
        self._reset_opens_if_flat(strategy_id, symbol, book)

    def try_fill(self, bar: BarData, book=None) -> list[TradeData]:
        if not bar.tradable:
            return []
        key_candidates = [
            k for k in self._pending
            if k[1] == bar.symbol.strip().lower()
        ]
        trades: list[TradeData] = []
        for key in key_candidates:
            sid, _ = key
            filled_set = self._open_filled.setdefault(key, set())
            remain: list[_Pending] = []
            long_rem = short_rem = float("inf")
            if book is not None:
                pos = book.get_strategy_position(sid, bar.symbol)
                long_rem = float(pos.long_qty)
                short_rem = float(pos.short_qty)
            for p in self._pending.get(key, []):
                open_key = (p.side, round(float(p.price), 4))
                if p.side in (OPEN_LONG, OPEN_SHORT) and open_key in filled_set:
                    remain.append(p)
                    continue
                if p.side == CLOSE_LONG and long_rem < float(p.volume) - 1e-9:
                    remain.append(p)
                    continue
                if p.side == CLOSE_SHORT and short_rem < float(p.volume) - 1e-9:
                    remain.append(p)
                    continue
                if not self._match(p, bar):
                    remain.append(p)
                    continue
                direction, offset = self._to_ctp(p.side)
                trades.append(TradeData(
                    strategy_id=sid,
                    symbol=p.symbol,
                    exchange=bar.exchange,
                    order_id=p.order_id,
                    trade_id=f"t-{next(_TRADE_ID)}",
                    direction=direction,
                    offset=offset,
                    price=p.price,
                    volume=p.volume,
                    datetime=bar.datetime,
                    trading_day=bar.trading_day,
                ))
                if p.side in (OPEN_LONG, OPEN_SHORT):
                    filled_set.add(open_key)
                    remain.append(p)
                    if p.side == OPEN_LONG:
                        long_rem += float(p.volume)
                    else:
                        short_rem += float(p.volume)
                elif p.side == CLOSE_LONG:
                    long_rem -= float(p.volume)
                elif p.side == CLOSE_SHORT:
                    short_rem -= float(p.volume)
            self._pending[key] = remain
        return trades

    @staticmethod
    def _to_ctp(side: str) -> tuple[str, str]:
        mapping = {
            OPEN_LONG: ("LONG", "OPEN"),
            OPEN_SHORT: ("SHORT", "OPEN"),
            CLOSE_LONG: ("SHORT", "CLOSE"),
            CLOSE_SHORT: ("LONG", "CLOSE"),
        }
        return mapping[side]

    @staticmethod
    def _book(bar: BarData) -> tuple[float, float, float, float]:
        """盘口 OHLC；缺失/NaN/全 0 时回退 comb OHLC（兼容旧测试 bar）。"""
        vals = (bar.bid_low, bar.bid_high, bar.ask_low, bar.ask_high)
        if all(math.isfinite(v) for v in vals) and any(v != 0.0 for v in vals):
            return vals
        return bar.low_price, bar.high_price, bar.low_price, bar.high_price

    @staticmethod
    def _match(p: _Pending, bar: BarData) -> bool:
        px = float(p.price)
        _, bid_hi, ask_lo, _ = ReconcileSimulator._book(bar)
        if p.side == OPEN_LONG:
            return ask_lo <= px
        if p.side == OPEN_SHORT:
            return bid_hi >= px
        if p.side == CLOSE_LONG:
            return bid_hi >= px
        if p.side == CLOSE_SHORT:
            return ask_lo <= px
        return False
