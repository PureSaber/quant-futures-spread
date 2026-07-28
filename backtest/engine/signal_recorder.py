"""backtest/engine/signal_recorder.py — TargetOrder → signals.csv。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pandas as pd

from framework.base import (
    TargetOrder, OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT,
)
from backtest.types import BarData

_COLUMNS: tuple[str, ...] = (
    "strategy_id", "symbol", "tradingday",
    "action_datetime", "bar_datetime",
    "signal_kind", "direction", "offset", "order_type",
    "price", "volume_hint", "session", "tag", "signal_id",
    "bar_open", "bar_high", "bar_low", "bar_close", "bar_volume", "bar_oi",
)

_SIDE_META = {
    OPEN_LONG: ("entry", 1, "open"),
    OPEN_SHORT: ("entry", -1, "open"),
    CLOSE_LONG: ("exit", -1, "close"),
    CLOSE_SHORT: ("exit", 1, "close"),
}


def strategy_short_name(module_path: str) -> str:
    parts = str(module_path or "").split(".")
    if len(parts) >= 2 and parts[0] == "strategies":
        return parts[1]
    return parts[-1] if parts else ""


def _signal_kind(target: TargetOrder) -> str:
    tag = (target.tag or "").lower()
    if "stop" in tag or "止损" in tag:
        return "stop_loss"
    if "tp" in tag or "take_profit" in tag or "止盈" in tag:
        return "take_profit"
    if "force" in tag or "强平" in tag:
        return "force_close"
    return _SIDE_META[target.side][0]


def _direction_offset(target: TargetOrder) -> tuple[int, str]:
    _, direction, offset = _SIDE_META[target.side]
    return direction, offset


@dataclass
class _Row:
    strategy_id: str
    symbol: str
    tradingday: str
    action_datetime: object
    bar_datetime: object
    signal_kind: str
    direction: int
    offset: str
    order_type: str
    price: float
    volume_hint: int
    session: str
    tag: str
    signal_id: str
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    bar_volume: float
    bar_oi: float


class SignalRecorder:
    """记录策略 reconcile 前的 TargetOrder 意图；同 (side, tag) 价量未变则跳过。"""

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id
        self._rows: list[_Row] = []
        self._last: dict[tuple[str, str, str], tuple[float, int]] = {}

    def record_targets(self, symbol: str, targets: list[TargetOrder], bar: BarData) -> None:
        sym = (symbol or "").strip()
        for t in targets:
            tag = t.tag or ""
            key = (sym, t.side, tag)
            state = (round(float(t.price), 4), int(round(float(t.volume))))
            if self._last.get(key) == state:
                continue
            self._last[key] = state
            kind = _signal_kind(t)
            direction, offset = _direction_offset(t)
            self._rows.append(_Row(
                strategy_id=self.strategy_id,
                symbol=sym,
                tradingday=str(bar.trading_day or ""),
                action_datetime=bar.datetime,
                bar_datetime=bar.datetime,
                signal_kind=kind,
                direction=direction,
                offset=offset,
                order_type="limit",
                price=float(t.price),
                volume_hint=state[1],
                session="",
                tag=tag,
                signal_id=uuid.uuid4().hex[:8],
                bar_open=float(bar.open_price),
                bar_high=float(bar.high_price),
                bar_low=float(bar.low_price),
                bar_close=float(bar.close_price),
                bar_volume=float(bar.volume),
                bar_oi=float(bar.open_interest),
            ))

    def to_records(self) -> list[dict]:
        if not self._rows:
            return []
        return [
            {
                "strategy_id": r.strategy_id,
                "symbol": r.symbol,
                "tradingday": r.tradingday,
                "action_datetime": r.action_datetime,
                "bar_datetime": r.bar_datetime,
                "signal_kind": r.signal_kind,
                "direction": r.direction,
                "offset": r.offset,
                "order_type": r.order_type,
                "price": r.price,
                "volume_hint": r.volume_hint,
                "session": r.session,
                "tag": r.tag,
                "signal_id": r.signal_id,
                "bar_open": r.bar_open,
                "bar_high": r.bar_high,
                "bar_low": r.bar_low,
                "bar_close": r.bar_close,
                "bar_volume": r.bar_volume,
                "bar_oi": r.bar_oi,
            }
            for r in self._rows
        ]

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=list(_COLUMNS))
        df = pd.DataFrame(self.to_records(), columns=list(_COLUMNS))
        return df.sort_values(
            ["strategy_id", "symbol", "action_datetime"], kind="mergesort",
        ).reset_index(drop=True)


def write_signals_csv(df: pd.DataFrame, path: str) -> None:
    out = df.copy()
    if out.empty:
        out.to_csv(path, index=False, encoding="utf-8-sig")
        return
    for col in ("action_datetime", "bar_datetime"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col]).dt.strftime("%Y-%m-%d %H:%M:%S")
    out.to_csv(path, index=False, encoding="utf-8-sig")
