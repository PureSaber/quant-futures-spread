"""Auditable spread-signal decomposition into public QExec leg intents."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quant_data_kit import FixedPoint, MarketEvent
from quant_execution import OrderIntent, OrderType, Side, StrategyContext, TimeInForce

_ACTIONS = frozenset({"open_long", "close_long", "open_short", "close_short"})


def _quantity(value: str | int, scale: int) -> FixedPoint:
    return FixedPoint.from_decimal(Decimal(str(value)), scale)


@dataclass(frozen=True, slots=True)
class SpreadSignal:
    signal_id: str
    trigger_event_id: str
    action: str
    leg_a: str
    leg_b: str
    quantity: FixedPoint

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or not self.trigger_event_id.strip():
            raise ValueError("signal_id and trigger_event_id are required")
        if self.action not in _ACTIONS:
            raise ValueError(f"unsupported spread action: {self.action}")
        if not self.leg_a.strip() or not self.leg_b.strip() or self.leg_a == self.leg_b:
            raise ValueError("spread legs must be distinct non-empty instrument IDs")
        if not self.quantity.is_positive():
            raise ValueError("spread quantity must be positive")

    @classmethod
    def from_config(cls, payload: dict, *, symbol_map: dict[str, str]) -> SpreadSignal:
        try:
            leg_a = symbol_map[payload["leg_a"]]
            leg_b = symbol_map[payload["leg_b"]]
        except KeyError as exc:
            raise ValueError(f"spread signal references an unknown fixture symbol: {exc}") from exc
        return cls(
            signal_id=str(payload["signal_id"]),
            trigger_event_id=str(payload["trigger_event_id"]),
            action=str(payload["action"]),
            leg_a=leg_a,
            leg_b=leg_b,
            quantity=_quantity(payload["quantity"], int(payload.get("quantity_scale", 0))),
        )


@dataclass(frozen=True, slots=True)
class LegIntentAudit:
    signal_id: str
    action: str
    leg_role: str
    instrument_id: str
    idempotency_key: str
    side: Side
    reduce_only: bool
    quantity: FixedPoint


class AuditedSpreadStrategy:
    """QExec Strategy whose only output is stable, leg-level ``OrderIntent`` facts."""

    sends_live_orders = False

    def __init__(self, signals: tuple[SpreadSignal, ...]) -> None:
        trigger_ids = [signal.trigger_event_id for signal in signals]
        signal_ids = [signal.signal_id for signal in signals]
        if len(trigger_ids) != len(set(trigger_ids)) or len(signal_ids) != len(set(signal_ids)):
            raise ValueError("spread signal and trigger identifiers must be unique")
        self._signals = {signal.trigger_event_id: signal for signal in signals}
        self.reset()

    def reset(self) -> None:
        self._emitted: set[str] = set()
        self._audit: list[LegIntentAudit] = []

    def capture_state(self) -> dict[str, object]:
        return {"emitted": set(self._emitted), "audit": list(self._audit)}

    def restore_state(self, state: dict[str, object]) -> None:
        self._emitted = set(state["emitted"])
        self._audit = list(state["audit"])

    @property
    def audit_trail(self) -> tuple[LegIntentAudit, ...]:
        return tuple(self._audit)

    @staticmethod
    def _directions(action: str) -> tuple[tuple[Side, bool], tuple[Side, bool]]:
        directions = {
            "open_long": ((Side.BUY, False), (Side.SELL, False)),
            "close_long": ((Side.SELL, True), (Side.BUY, True)),
            "open_short": ((Side.SELL, False), (Side.BUY, False)),
            "close_short": ((Side.BUY, True), (Side.SELL, True)),
        }
        return directions[action]

    def on_event(self, context: StrategyContext, event: MarketEvent) -> tuple[OrderIntent, ...]:
        signal = self._signals.get(event.event_id)
        if signal is None or signal.signal_id in self._emitted:
            return ()
        self._emitted.add(signal.signal_id)
        intents: list[OrderIntent] = []
        for leg_role, instrument_id, (side, reduce_only) in zip(
            ("leg-a", "leg-b"),
            (signal.leg_a, signal.leg_b),
            self._directions(signal.action),
            strict=True,
        ):
            key = f"{context.run_id}:{signal.signal_id}:{leg_role}"
            intent = OrderIntent(
                idempotency_key=key,
                account_id=context.account_id,
                strategy_id=context.strategy_id,
                instrument_id=instrument_id,
                side=side,
                quantity=signal.quantity,
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                created_at=event.available_at,
                reduce_only=reduce_only,
            )
            intents.append(intent)
            self._audit.append(
                LegIntentAudit(
                    signal_id=signal.signal_id,
                    action=signal.action,
                    leg_role=leg_role,
                    instrument_id=instrument_id,
                    idempotency_key=key,
                    side=side,
                    reduce_only=reduce_only,
                    quantity=signal.quantity,
                )
            )
        return tuple(intents)
