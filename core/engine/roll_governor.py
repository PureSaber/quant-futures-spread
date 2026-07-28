"""core/engine/roll_governor.py — defer_until_flat 换月状态机。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from framework.base import OPEN_LONG, OPEN_SHORT, TargetOrder

_OPEN_SIDES = {OPEN_LONG, OPEN_SHORT}


class RollState(str, Enum):
    NORMAL = "normal"
    CLOSING_OUT = "closing_out"


@dataclass
class RollEvent:
    tradingday: str
    old_symbol: str
    new_symbol: str
    reason: str


@dataclass
class RollGovernor:
    trade_symbol: str
    state: RollState = RollState.NORMAL
    events: list[RollEvent] = field(default_factory=list)

    def effective_symbol(self, calendar_symbol: str, net_qty: float,
                         tradingday: str = "") -> str:
        cal = str(calendar_symbol or "").strip()
        if not cal:
            return self.trade_symbol
        if self.state == RollState.CLOSING_OUT:
            return self.trade_symbol
        if cal != self.trade_symbol:
            if abs(net_qty) < 1e-9:
                self._switch(cal, "flat_switch", tradingday)
            else:
                self.entering_closing_out(cal, tradingday)
        return self.trade_symbol

    def after_bar(self, calendar_symbol: str, net_qty: float, tradingday: str) -> None:
        cal = str(calendar_symbol or "").strip()
        if self.state == RollState.CLOSING_OUT and abs(net_qty) < 1e-9:
            self.state = RollState.NORMAL
            if cal and cal != self.trade_symbol:
                self._switch(cal, "closed_out", tradingday)

    def filter_targets(self, targets: list[TargetOrder]) -> list[TargetOrder]:
        if self.state != RollState.CLOSING_OUT:
            return targets
        return [t for t in targets if t.side not in _OPEN_SIDES]

    def entering_closing_out(self, calendar_symbol: str, tradingday: str) -> None:
        if self.state == RollState.CLOSING_OUT:
            return
        self.state = RollState.CLOSING_OUT
        self.events.append(RollEvent(
            tradingday=tradingday,
            old_symbol=self.trade_symbol,
            new_symbol=calendar_symbol,
            reason="enter_closing_out",
        ))

    def _switch(self, new_symbol: str, reason: str, tradingday: str) -> None:
        if new_symbol == self.trade_symbol:
            return
        self.events.append(RollEvent(
            tradingday=tradingday or "",
            old_symbol=self.trade_symbol,
            new_symbol=new_symbol,
            reason=reason,
        ))
        self.trade_symbol = new_symbol
