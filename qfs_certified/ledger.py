"""Read-only snapshot capture around the frozen QExec exact ledger."""

from __future__ import annotations

from datetime import datetime

from quant_data_kit import MarketEvent
from quant_execution import AccountSnapshot, ExactAccountLedger, LedgerEvent


class SnapshotRecordingLedger(ExactAccountLedger):
    """ExactAccountLedger with reporting snapshots; accounting behavior is unchanged."""

    def reset(self, *, opened_at: datetime | None = None) -> None:
        super().reset(opened_at=opened_at)
        self._reporting_snapshots: dict[object, AccountSnapshot] = {}

    def _record(self, event_time) -> AccountSnapshot:
        snapshot = self.snapshot(event_time)
        self._reporting_snapshots[event_time] = snapshot
        return snapshot

    @property
    def reporting_snapshots(self) -> tuple[AccountSnapshot, ...]:
        return tuple(self._reporting_snapshots[key] for key in sorted(self._reporting_snapshots))

    def observe_market(
        self,
        event: MarketEvent,
        *,
        create_snapshot: bool = True,
        trusted_unique: bool = False,
    ) -> AccountSnapshot | None:
        result = super().observe_market(
            event,
            create_snapshot=create_snapshot,
            trusted_unique=trusted_unique,
        )
        recorded = self._record(event.available_at)
        return recorded if create_snapshot else result

    def apply(self, event: LedgerEvent, *, create_snapshot: bool = True) -> AccountSnapshot | None:
        result = super().apply(event, create_snapshot=create_snapshot)
        recorded = self._record(event.event_time)
        return recorded if create_snapshot else result

    def apply_with_trading_day(
        self,
        event: LedgerEvent,
        *,
        trading_day,
        create_snapshot: bool = True,
    ) -> AccountSnapshot | None:
        result = super().apply_with_trading_day(
            event,
            trading_day=trading_day,
            create_snapshot=create_snapshot,
        )
        recorded = self._record(event.event_time)
        return recorded if create_snapshot else result
