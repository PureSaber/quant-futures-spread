"""Versioned PIT reference-data loader for repository-only futures fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from quant_data_kit import (
    AssetClass,
    FixedPoint,
    InstrumentSpec,
    MarginMode,
    SymbolMapping,
    ensure_utc_datetime,
)

FIXTURE_CERTIFICATION = "fixture-certified"
REQUIRED_METADATA = frozenset(
    {
        "initial_margin_rate",
        "maintenance_margin_rate",
        "fee_rate",
        "close_today_fee_rate",
        "open_close_model",
        "close_today_rule",
        "night_session_trading_day_rule",
        "daily_settlement_rule",
        "roll_rule",
        "fixture_scope",
        "historical_claim",
    }
)


def parse_utc(value: str, field: str) -> datetime:
    """Parse one explicit UTC timestamp without accepting naive or non-UTC values."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    return ensure_utc_datetime(parsed, field=field)


def fixed(value: str | int, scale: int) -> FixedPoint:
    return FixedPoint.from_decimal(Decimal(str(value)), int(scale))


@dataclass(frozen=True)
class FixtureMaster:
    schema_version: str
    certification: str
    applicability: str
    instruments: dict[str, InstrumentSpec]
    mappings: tuple[SymbolMapping, ...]

    def resolve(self, source: str, provider_symbol: str, as_of: datetime) -> str:
        as_of = ensure_utc_datetime(as_of, field="as_of")
        matches = [
            item
            for item in self.mappings
            if item.source == source
            and item.provider_symbol == provider_symbol
            and item.available_at <= as_of
            and (item.superseded_at is None or as_of < item.superseded_at)
            and item.effective_from <= as_of
            and (item.effective_to is None or as_of < item.effective_to)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"PIT mapping must resolve exactly once for {source}:{provider_symbol}; "
                f"got {len(matches)}"
            )
        return matches[0].instrument_id


def _instrument(record: dict) -> InstrumentSpec:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict) or not REQUIRED_METADATA.issubset(metadata):
        missing = sorted(REQUIRED_METADATA - set(metadata or {}))
        raise ValueError(f"fixture InstrumentSpec metadata is incomplete: {missing}")
    if metadata["historical_claim"] != "none":
        raise ValueError("fixture master must not claim real listing history")
    expiry = record.get("expiry_date")
    return InstrumentSpec(
        instrument_id=record["instrument_id"],
        asset_class=AssetClass(record["asset_class"]),
        product_type=record["product_type"],
        venue=record["venue"],
        native_symbol=record["native_symbol"],
        settlement_currency=record["settlement_currency"],
        price_tick=fixed(record["price_tick"], record["price_scale"]),
        quantity_step=fixed(record["quantity_step"], record["quantity_scale"]),
        contract_multiplier=fixed(record["contract_multiplier"], record["multiplier_scale"]),
        calendar_id=record["calendar_id"],
        margin_mode=MarginMode(record["margin_mode"]),
        effective_from=parse_utc(record["effective_from"], "effective_from"),
        effective_to=(
            parse_utc(record["effective_to"], "effective_to")
            if record.get("effective_to")
            else None
        ),
        available_at=parse_utc(record["available_at"], "available_at"),
        superseded_at=(
            parse_utc(record["superseded_at"], "superseded_at")
            if record.get("superseded_at")
            else None
        ),
        expiry_date=date.fromisoformat(expiry) if expiry else None,
        metadata=metadata,
    )


def _mapping(record: dict) -> SymbolMapping:
    return SymbolMapping(
        source=record["source"],
        provider_symbol=record["provider_symbol"],
        instrument_id=record["instrument_id"],
        effective_from=parse_utc(record["effective_from"], "effective_from"),
        effective_to=(
            parse_utc(record["effective_to"], "effective_to")
            if record.get("effective_to")
            else None
        ),
        available_at=parse_utc(record["available_at"], "available_at"),
        superseded_at=(
            parse_utc(record["superseded_at"], "superseded_at")
            if record.get("superseded_at")
            else None
        ),
    )


def load_fixture_master(path: str | Path, *, as_of: datetime) -> FixtureMaster:
    """Load only reference facts that were effective and knowable at ``as_of``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "fixture-cn-futures-v1":
        raise ValueError("unsupported fixture master schema")
    if payload.get("certification") != FIXTURE_CERTIFICATION:
        raise ValueError("certified backtests require fixture-certified reference data")
    as_of = ensure_utc_datetime(as_of, field="as_of")
    defaults = payload.get("instrument_defaults") or {}
    default_metadata = defaults.get("metadata") or {}
    all_instruments = [
        _instrument(
            {
                **defaults,
                **item,
                "metadata": {**default_metadata, **(item.get("metadata") or {})},
            }
        )
        for item in payload.get("instruments", [])
    ]
    instruments = {
        item.instrument_id: item
        for item in all_instruments
        if item.available_at <= as_of
        and (item.superseded_at is None or as_of < item.superseded_at)
        and item.effective_from <= as_of
        and (item.effective_to is None or as_of < item.effective_to)
    }
    mapping_defaults = payload.get("symbol_mapping_defaults") or {}
    mappings = tuple(
        item
        for item in (
            _mapping({**mapping_defaults, **record})
            for record in payload.get("symbol_mappings", [])
        )
        if item.instrument_id in instruments
        and item.available_at <= as_of
        and (item.superseded_at is None or as_of < item.superseded_at)
        and item.effective_from <= as_of
        and (item.effective_to is None or as_of < item.effective_to)
    )
    if not instruments or not mappings:
        raise ValueError("fixture master has no PIT-visible instruments and mappings")
    if {item.instrument_id for item in mappings} != set(instruments):
        raise ValueError("every PIT-visible fixture instrument requires a SymbolMapping")
    return FixtureMaster(
        schema_version=payload["schema_version"],
        certification=payload["certification"],
        applicability=payload["applicability"],
        instruments=instruments,
        mappings=mappings,
    )
