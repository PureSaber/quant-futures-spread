"""MarketEvent construction for the immutable local certified fixture."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta, timezone
from pathlib import Path

from quant_data_kit import BarEvent, MarketEvent, StatusEvent

from qfs_certified.reference import FIXTURE_CERTIFICATION, FixtureMaster, fixed, parse_utc

LOCAL_SAMPLE_SOURCE = "qfs-local-sample-v1"


@dataclass(frozen=True)
class EventFixture:
    schema_version: str
    certification: str
    applicability: str
    events: tuple[MarketEvent, ...]


def _base(record: dict, master: FixtureMaster) -> dict:
    if record.get("sequence") is None:
        raise ValueError(f"event {record.get('event_id', '<unknown>')} requires sequence")
    event_time = parse_utc(record["event_time"], "event_time")
    received_at = parse_utc(record.get("received_at", record["event_time"]), "received_at")
    available_at = parse_utc(
        record.get("available_at", record.get("received_at", record["event_time"])),
        "available_at",
    )
    source = record.get("source", LOCAL_SAMPLE_SOURCE)
    instrument_id = master.resolve(source, record["provider_symbol"], available_at)
    return {
        "event_id": record["event_id"],
        "instrument_id": instrument_id,
        "event_time": event_time,
        "received_at": received_at,
        "available_at": available_at,
        "source": source,
        "trading_day": date.fromisoformat(record["trading_day"]),
        "session_id": record["session_id"],
        "sequence": int(record["sequence"]),
    }


def _event(record: dict, master: FixtureMaster) -> MarketEvent:
    common = _base(record, master)
    if record["event_type"] == "status":
        return StatusEvent(
            **common,
            status=record["status"],
            reason=record.get("reason", ""),
        )
    if record["event_type"] != "bar":
        raise ValueError(f"unsupported fixture event_type: {record['event_type']}")
    spec = master.instruments[common["instrument_id"]]
    return BarEvent(
        **common,
        bar_start=(
            parse_utc(record["bar_start"], "bar_start")
            if record.get("bar_start")
            else common["event_time"] - timedelta(minutes=1)
        ),
        bar_end=common["event_time"],
        open_price=fixed(record["open"], spec.price_tick.scale),
        high_price=fixed(record["high"], spec.price_tick.scale),
        low_price=fixed(record["low"], spec.price_tick.scale),
        close_price=fixed(record["close"], spec.price_tick.scale),
        volume=fixed(record["volume"], spec.quantity_step.scale),
        is_complete=True,
    )


def _validate_night_trading_day(events: tuple[MarketEvent, ...]) -> None:
    china = timezone(timedelta(hours=8))
    night_events = [event for event in events if event.session_id.startswith("fixture-night-")]
    if not night_events:
        raise ValueError("fixture must include a night-session trading-day boundary")
    for event in night_events:
        local_date = event.event_time.astimezone(china).date()
        if event.trading_day <= local_date:
            raise ValueError("night-session fixture must map to the next trading day")


def load_event_fixture(path: str | Path, *, master: FixtureMaster) -> EventFixture:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "fixture-cn-futures-events-v1":
        raise ValueError("unsupported event fixture schema")
    if payload.get("certification") != FIXTURE_CERTIFICATION:
        raise ValueError("certified backtests require fixture-certified market events")
    defaults = payload.get("event_defaults") or {}
    events = tuple(_event({**defaults, **record}, master) for record in payload.get("events", []))
    if not events:
        raise ValueError("event fixture is empty")
    event_ids = [event.event_id for event in events]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("event fixture contains duplicate event_id values")
    sequences = [event.sequence for event in events]
    if any(sequence is None for sequence in sequences):
        raise ValueError("event fixture requires a non-null sequence for every event")
    if len(sequences) != len(set(sequences)):
        raise ValueError("event fixture contains duplicate sequence values")
    if sequences != sorted(sequences):
        raise ValueError("event fixture contains out-of-order sequence values")
    expected = list(range(1, len(events) + 1))
    if sequences != expected:
        raise ValueError(
            "event fixture sequence must be contiguous from 1 without gaps "
            f"(expected {expected[0]}..{expected[-1]})"
        )
    _validate_night_trading_day(events)
    return EventFixture(
        schema_version=payload["schema_version"],
        certification=payload["certification"],
        applicability=payload["applicability"],
        events=events,
    )
