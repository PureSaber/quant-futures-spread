"""Read-only preflight for the repository's explicit synthetic futures fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from qfs_certified.runner import prepare_certified_inputs


def preflight(config_path: str | Path) -> dict[str, Any]:
    prepared = prepare_certified_inputs(config_path)
    events = prepared.event_fixture.events
    hashes = {name: state["sha256"] for name, state in prepared.input_state.items()}
    fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": "quant-futures-spread.preflight/v1",
        "status": "pass",
        "read_only": True,
        "investable": False,
        "evidence_kind": "synthetic",
        "scope": "fixture-only / backtest-only",
        "run_id": prepared.config["run_id"],
        "base_currency": prepared.config["account"]["base_currency"],
        "initial_cash": str(prepared.initial_cash.to_decimal()),
        "event_count": len(events),
        "signal_count": len(prepared.signals),
        "instrument_ids": sorted({event.instrument_id for event in events}),
        "available_start": min(event.available_at for event in events).isoformat(),
        "available_end": max(event.available_at for event in events).isoformat(),
        "input_fingerprint": fingerprint,
        "inputs": prepared.input_state,
        "checks": ["config", "fixture_master", "events", "signal_references", "input_stability"],
        "limitations": [
            "No strategy, execution engine or account ledger is created.",
            "Fills, pair completion, liquidity and margin remain replay-time checks.",
            "Passing does not certify real historical market data or lock future inputs.",
        ],
    }
