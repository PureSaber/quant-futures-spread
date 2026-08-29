from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
import yaml
from quant_data_kit import (
    BarEvent,
    FixedPoint,
    MarkPriceEvent,
    QuoteEvent,
    StatusEvent,
    TradeEvent,
)
from quant_execution import AccountSnapshot, Side, StrategyContext
from quant_lab import load_and_validate_standard_run

from qfs_certified.events import load_event_fixture
from qfs_certified.reference import FixtureMaster, load_fixture_master, parse_utc
from qfs_certified.runner import execute_certified_replay, run_certified_backtest
from qfs_certified.standard_v2 import _event_price, _frame, _reporting_frames
from qfs_certified.strategy import AuditedSpreadStrategy, SpreadSignal

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "certified_local_sample_v1.yaml"
MASTER = ROOT / "data" / "local_sample" / "certified_v1" / "instrument_master.json"
EVENTS = ROOT / "data" / "local_sample" / "certified_v1" / "market_events.json"
AS_OF = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
CODE_VERSION = "70cbbbb90c6e8d6835f14c627e4dd44a1f6ae83d"


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_release_version_and_internal_dependencies_are_frozen() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.3.1"' in project
    assert "quant-data-kit.git@v0.6.1" in project
    assert "quant-execution.git@v0.4.1" in project
    assert "quant-lab.git@v0.3.1" in project


def _config(tmp_path: Path, **changes) -> Path:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if key == "initial_cash":
            payload["account"]["initial_cash"] = value
        else:
            payload[key] = value
    path = tmp_path / "certified.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_fixture_master_is_pit_versioned_and_explicit() -> None:
    master = load_fixture_master(MASTER, as_of=AS_OF)
    assert master.certification == "fixture-certified"
    assert len(master.instruments) == len(master.mappings) == 8
    assert "not an exchange listing-history claim" in master.applicability
    for spec in master.instruments.values():
        assert spec.venue == "DCE"
        assert spec.settlement_currency == "CNY"
        assert spec.price_tick == FixedPoint(1, 0)
        assert spec.quantity_step == FixedPoint(1, 0)
        assert spec.contract_multiplier == FixedPoint(10, 0)
        assert spec.metadata["historical_claim"] == "none"
        assert spec.metadata["open_close_model"] == "qexec-reduce-only-auto-fifo"
        assert "next-fixture-trading-day" in spec.metadata["night_session_trading_day_rule"]
        assert "daily" in spec.metadata["daily_settlement_rule"]
        assert spec.metadata["roll_successor"]
    assert master.resolve("qfs-local-sample-v1", "A2003", AS_OF).endswith("A2003")
    with pytest.raises(ValueError, match="no PIT-visible"):
        load_fixture_master(MASTER, as_of=datetime(2019, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="exactly once"):
        master.resolve("qfs-local-sample-v1", "missing", AS_OF)
    duplicate = replace(master, mappings=master.mappings + (master.mappings[0],))
    with pytest.raises(ValueError, match="got 2"):
        duplicate.resolve("qfs-local-sample-v1", "A2003", AS_OF)


def test_fixture_master_rejects_bad_schema_certification_and_claims(tmp_path: Path) -> None:
    baseline = json.loads(MASTER.read_text(encoding="utf-8"))
    cases = (
        ({**baseline, "schema_version": "v0"}, "schema"),
        ({**baseline, "certification": "real-history"}, "fixture-certified"),
        ({**baseline, "instruments": [], "symbol_mappings": []}, "no PIT-visible"),
    )
    for index, (payload, message) in enumerate(cases):
        with pytest.raises(ValueError, match=message):
            load_fixture_master(_write_json(tmp_path / f"bad-{index}.json", payload), as_of=AS_OF)

    claim = json.loads(MASTER.read_text(encoding="utf-8"))
    claim["instrument_defaults"]["metadata"]["historical_claim"] = "real"
    with pytest.raises(ValueError, match="must not claim"):
        load_fixture_master(_write_json(tmp_path / "claim.json", claim), as_of=AS_OF)

    incomplete = json.loads(MASTER.read_text(encoding="utf-8"))
    del incomplete["instrument_defaults"]["metadata"]["roll_rule"]
    with pytest.raises(ValueError, match="incomplete"):
        load_fixture_master(_write_json(tmp_path / "incomplete.json", incomplete), as_of=AS_OF)

    with pytest.raises(ValueError, match="ISO-8601"):
        parse_utc("bad", "time")
    with pytest.raises(Exception, match="UTC"):
        parse_utc("2020-01-01T00:00:00", "time")


def test_market_fixture_covers_night_hold_close_and_roll() -> None:
    master = load_fixture_master(MASTER, as_of=AS_OF)
    fixture = load_event_fixture(EVENTS, master=master)
    assert fixture.certification == "fixture-certified"
    assert len(fixture.events) == 23
    assert [event.sequence for event in fixture.events] == list(range(1, 24))
    settlements = [
        event for event in fixture.events if getattr(event, "status", "") == "daily_settlement"
    ]
    assert [event.event_id for event in settlements] == [
        "daily-settlement-old-a",
        "daily-settlement-old-b",
    ]
    assert all(event.trading_day.isoformat() == "2020-01-03" for event in settlements)
    assert [event.event_id for event in fixture.events].index("signal-close-old") < [
        event.event_id for event in fixture.events
    ].index("roll-boundary-old-to-new")
    night = [event for event in fixture.events if event.session_id.startswith("fixture-night-")]
    assert night
    assert all(event.trading_day.isoformat() == "2020-01-03" for event in night)
    assert {event.event_id for event in fixture.events} >= {
        "signal-open-old",
        "hold-old-a",
        "signal-close-old",
        "roll-boundary-old-to-new",
        "signal-open-new",
        "signal-close-new",
    }


def test_market_fixture_fails_closed_on_mutation(tmp_path: Path) -> None:
    master = load_fixture_master(MASTER, as_of=AS_OF)
    baseline = json.loads(EVENTS.read_text(encoding="utf-8"))
    cases = []
    wrong_schema = {**baseline, "schema_version": "v0"}
    cases.append((wrong_schema, "schema"))
    wrong_cert = {**baseline, "certification": "unverified"}
    cases.append((wrong_cert, "fixture-certified"))
    empty = {**baseline, "events": []}
    cases.append((empty, "empty"))
    duplicate = json.loads(EVENTS.read_text(encoding="utf-8"))
    duplicate["events"].append(dict(duplicate["events"][0]))
    cases.append((duplicate, "duplicate"))
    unsupported = json.loads(EVENTS.read_text(encoding="utf-8"))
    unsupported["events"][0]["event_type"] = "quote"
    cases.append((unsupported, "unsupported"))
    wrong_day = json.loads(EVENTS.read_text(encoding="utf-8"))
    wrong_day["event_defaults"]["trading_day"] = "2020-01-02"
    cases.append((wrong_day, "next trading day"))
    for index, (payload, message) in enumerate(cases):
        with pytest.raises(ValueError, match=message):
            load_event_fixture(
                _write_json(tmp_path / f"events-{index}.json", payload), master=master
            )

    for index, (mutator, message) in enumerate(
        (
            (
                lambda payload: payload["events"][-1].__setitem__("sequence", 99),
                "sequence must be contiguous",
            ),
            (
                lambda payload: (
                    payload["events"][5].__setitem__("sequence", 7),
                    payload["events"][6].__setitem__("sequence", 6),
                ),
                "out-of-order sequence",
            ),
            (
                lambda payload: payload["events"][5].__setitem__("sequence", 4),
                "duplicate sequence",
            ),
        )
    ):
        payload = json.loads(EVENTS.read_text(encoding="utf-8"))
        mutator(payload)
        with pytest.raises(ValueError, match=message):
            load_event_fixture(
                _write_json(tmp_path / f"sequence-{index}.json", payload), master=master
            )


def _status(event_id: str) -> StatusEvent:
    now = datetime(2020, 1, 2, 13, tzinfo=timezone.utc)
    return StatusEvent(
        event_id=event_id,
        instrument_id="future:fixture-dce:A2003",
        event_time=now,
        received_at=now,
        available_at=now,
        source="fixture",
        trading_day=now.date(),
        session_id="fixture",
        sequence=1,
        status="open",
    )


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("open_long", ((Side.BUY, False), (Side.SELL, False))),
        ("close_long", ((Side.SELL, True), (Side.BUY, True))),
        ("open_short", ((Side.SELL, False), (Side.BUY, False))),
        ("close_short", ((Side.BUY, True), (Side.SELL, True))),
    ],
)
def test_spread_strategy_emits_stable_leg_intents(action, expected) -> None:
    signal = SpreadSignal(
        signal_id=f"signal-{action}",
        trigger_event_id=f"trigger-{action}",
        action=action,
        leg_a="future:fixture-dce:A2003",
        leg_b="future:fixture-dce:B2003",
        quantity=FixedPoint(1, 0),
    )
    strategy = AuditedSpreadStrategy((signal,))
    context = StrategyContext(
        run_id="run",
        account_id="account",
        strategy_id="strategy",
        seed=7,
        state={},
    )
    event = _status(signal.trigger_event_id)
    intents = strategy.on_event(context, event)
    assert tuple((item.side, item.reduce_only) for item in intents) == expected
    assert [item.idempotency_key for item in intents] == [
        f"run:signal-{action}:leg-a",
        f"run:signal-{action}:leg-b",
    ]
    assert strategy.on_event(context, event) == ()
    state = strategy.capture_state()
    strategy.reset()
    assert not strategy.audit_trail
    strategy.restore_state(state)
    assert len(strategy.audit_trail) == 2


def test_spread_strategy_validation_is_fail_closed() -> None:
    base = {
        "signal_id": "s",
        "trigger_event_id": "e",
        "action": "open_long",
        "leg_a": "a",
        "leg_b": "b",
        "quantity": FixedPoint(1, 0),
    }
    for changes, message in (
        ({"signal_id": ""}, "required"),
        ({"action": "roll"}, "unsupported"),
        ({"leg_b": "a"}, "distinct"),
        ({"quantity": FixedPoint(0, 0)}, "positive"),
    ):
        with pytest.raises(ValueError, match=message):
            SpreadSignal(**(base | changes))
    with pytest.raises(ValueError, match="unknown"):
        SpreadSignal.from_config(
            {
                "signal_id": "s",
                "trigger_event_id": "e",
                "action": "open_long",
                "leg_a": "missing",
                "leg_b": "B",
                "quantity": "1",
            },
            symbol_map={"B": "b"},
        )
    signal = SpreadSignal(**base)
    with pytest.raises(ValueError, match="unique"):
        AuditedSpreadStrategy((signal, replace(signal, signal_id="other")))


def test_qexec_golden_replay_is_deterministic_and_reconciled() -> None:
    runs = [execute_certified_replay(CONFIG) for _ in range(3)]
    hashes = {
        (
            run.result.event_sha256,
            run.result.fill_sha256,
            run.result.ledger_sha256,
            run.result.result_sha256,
        )
        for run in runs
    }
    assert len(hashes) == 1
    replay = runs[0]
    assert replay.result.order_count == replay.result.fill_count == 8
    assert len(replay.artifacts.settlements) == 2
    assert [settlement.amount.to_decimal() for settlement in replay.artifacts.settlements] == [
        Decimal("20.00000000"),
        Decimal("20.00000000"),
    ]
    assert [settlement.event_time.isoformat() for settlement in replay.artifacts.settlements] == [
        "2020-01-03T00:59:00+00:00",
        "2020-01-03T00:59:01+00:00",
    ]
    settlement_transactions = [
        transaction
        for transaction in replay.artifacts.ledger_transactions
        if transaction.event_type.value == "settlement"
    ]
    assert len(settlement_transactions) == len(replay.artifacts.settlements) == 2
    assert {transaction.reference_id for transaction in settlement_transactions} == {
        settlement.settlement_id for settlement in replay.artifacts.settlements
    }
    assert len(replay.strategy.audit_trail) == 8
    assert not replay.artifacts.risk_events
    assert not replay.strategy.sends_live_orders
    assert replay.ledger.sends_live_orders is False
    assert all(order.filled_quantity == order.intent.quantity for order in replay.artifacts.orders)
    assert sum(fill.quantity.units for fill in replay.artifacts.fills) == sum(
        order.filled_quantity.units for order in replay.artifacts.orders
    )
    assert all(value.units == 0 for value in replay.ledger.snapshot().positions.values())
    assert any(snapshot.initial_margin.units > 0 for snapshot in replay.ledger.reporting_snapshots)
    close_today = [fee for fee in replay.artifacts.fees if "today=1" in fee.fee_type]
    assert len(close_today) == 4
    assert all(fee.amount.units > 0 for fee in close_today)
    for transaction in replay.artifacts.ledger_transactions:
        for currency in {posting.currency for posting in transaction.postings}:
            assert (
                sum(
                    posting.amount.to_decimal()
                    for posting in transaction.postings
                    if posting.currency == currency
                )
                == 0
            )


def test_margin_and_reduce_only_gates_reject_without_mutation(tmp_path: Path) -> None:
    low_cash = execute_certified_replay(
        _config(
            tmp_path,
            initial_cash="1000",
            signals=[
                {
                    "signal_id": "open",
                    "trigger_event_id": "signal-open-old",
                    "action": "open_long",
                    "leg_a": "A2003",
                    "leg_b": "B2003",
                    "quantity": "1",
                }
            ],
        )
    )
    assert low_cash.result.order_count == 2
    assert low_cash.result.fill_count == 0
    assert all("INSUFFICIENT_MARGIN" in event for event in low_cash.artifacts.risk_events)
    assert not low_cash.ledger.snapshot().positions

    close_only = execute_certified_replay(
        _config(
            tmp_path,
            signals=[
                {
                    "signal_id": "close",
                    "trigger_event_id": "signal-close-old",
                    "action": "close_long",
                    "leg_a": "A2003",
                    "leg_b": "B2003",
                    "quantity": "1",
                }
            ],
        )
    )
    assert close_only.result.fill_count == 0
    assert all("REDUCE_ONLY_VIOLATION" in event for event in close_only.artifacts.risk_events)
    assert not close_only.ledger.snapshot().positions


def test_standard_v2_is_complete_readable_and_quantity_conserving(tmp_path: Path) -> None:
    completed = run_certified_backtest(CONFIG, tmp_path, code_version=CODE_VERSION)
    loaded = load_and_validate_standard_run(completed.run_dir)
    assert loaded == completed.manifest
    assert loaded.profile == "backtest-ledger"
    assert loaded.internal_dependencies == {
        "quant-data-kit": "v0.6.1",
        "quant-execution": "v0.4.1",
        "quant-lab": "v0.3.1",
    }
    assert loaded.time_range == {
        "start": "2020-01-02T13:00:10+00:00",
        "end": "2020-01-03T01:07:10+00:00",
    }
    assert "1970" not in str(loaded.time_range)
    required = {record.name for record in loaded.artifacts if record.required}
    assert required == {
        "config",
        "metrics",
        "returns",
        "positions",
        "portfolio_snapshots",
        "exposures",
        "orders",
        "order_events",
        "fills",
        "costs",
        "cash_ledger",
        "margin",
    }
    base = completed.run_dir / "standard" / "v2"
    orders = pd.read_parquet(base / "orders.parquet")
    fills = pd.read_parquet(base / "fills.parquet")
    costs = pd.read_parquet(base / "costs.parquet")
    ledger = pd.read_parquet(base / "cash_ledger.parquet")
    metrics = json.loads((base / "metrics.json").read_text(encoding="utf-8"))
    assert len(orders) == len(fills) == len(costs) == 8
    assert orders["filled_quantity_units"].sum() == fills["quantity_units"].sum()
    balances = ledger.groupby(["transaction_id", "currency"])["amount_units"].sum()
    assert (balances == 0).all()
    assert (ledger[ledger["event_type"] == "settlement"].shape[0]) > 0
    assert metrics["settlement_count"] == 2
    with pytest.raises(FileExistsError, match="immutable"):
        run_certified_backtest(CONFIG, tmp_path, code_version=CODE_VERSION)


def test_margin_decomposition_must_equal_every_qexec_snapshot() -> None:
    replay = execute_certified_replay(CONFIG)
    frames = _reporting_frames(
        artifacts=replay.artifacts,
        snapshots=replay.ledger.reporting_snapshots,
        master=replay.master,
        strategy_id="certified_spread_v1",
        money_scale=8,
    )
    margin = frames["margin"]
    expected = {
        snapshot.event_time: (
            snapshot.initial_margin.to_decimal(),
            snapshot.maintenance_margin.to_decimal(),
        )
        for snapshot in replay.ledger.reporting_snapshots
    }
    margin_by_time = {
        event_time.to_pydatetime(): rows for event_time, rows in margin.groupby("event_time")
    }
    for event_time, (initial, maintenance) in expected.items():
        rows = margin_by_time.get(event_time, margin.iloc[0:0])
        assert (
            sum(
                FixedPoint(int(row.initial_margin_units), int(row.margin_scale)).to_decimal()
                for row in rows.itertuples()
            )
            == initial
        )
        assert (
            sum(
                FixedPoint(int(row.maintenance_margin_units), int(row.margin_scale)).to_decimal()
                for row in rows.itertuples()
            )
            == maintenance
        )


def test_standard_v2_adapter_covers_event_and_validation_boundaries() -> None:
    now = datetime(2020, 1, 2, 13, tzinfo=timezone.utc)
    common = {
        "event_id": "boundary",
        "instrument_id": "future:fixture-dce:A2003",
        "event_time": now,
        "received_at": now,
        "available_at": now,
        "source": "fixture",
        "trading_day": now.date(),
        "session_id": "fixture",
        "sequence": 1,
    }
    price = FixedPoint(100, 0)
    bar = BarEvent(
        **common,
        bar_start=now - timedelta(minutes=1),
        bar_end=now,
        open_price=price,
        high_price=price,
        low_price=price,
        close_price=price,
        volume=FixedPoint(1, 0),
        is_complete=True,
    )
    trade = TradeEvent(**common, price=price, quantity=FixedPoint(1, 0))
    mark = MarkPriceEvent(**common, price=price)
    quote = QuoteEvent(
        **common,
        bid_price=price,
        bid_quantity=FixedPoint(1, 0),
        ask_price=FixedPoint(102, 0),
        ask_quantity=FixedPoint(1, 0),
    )
    assert _frame("returns", []).empty
    assert _event_price(bar) == price
    assert _event_price(trade) == price
    assert _event_price(mark) == price
    assert _event_price(quote) == FixedPoint(101, 0)
    assert _event_price(StatusEvent(**common, status="open")) is None

    replay = execute_certified_replay(CONFIG)
    empty_artifacts = replace(replay.artifacts, market_events=(), fees=())
    first = AccountSnapshot(
        account_id="account",
        event_time=now,
        base_currency="CNY",
        cash_balances={"CNY": FixedPoint(1, 0)},
        nav=FixedPoint(0, 0),
        initial_margin=FixedPoint(0, 8),
        maintenance_margin=FixedPoint(0, 8),
    )
    second = replace(first, event_time=now.replace(minute=1), nav=FixedPoint(1, 0))
    with pytest.raises(ValueError, match="base-currency-only"):
        _reporting_frames(
            artifacts=empty_artifacts,
            snapshots=[
                replace(first, cash_balances={"CNY": FixedPoint(1, 0), "USD": FixedPoint(1, 0)})
            ],
            master=replay.master,
            strategy_id="boundary",
            money_scale=8,
        )
    with pytest.raises(ValueError, match="zero NAV"):
        _reporting_frames(
            artifacts=empty_artifacts,
            snapshots=[first, second],
            master=replay.master,
            strategy_id="boundary",
            money_scale=8,
        )

    first_id = next(iter(replay.master.instruments))
    original = replay.master.instruments[first_id]
    drifted = replace(
        original,
        metadata={**original.metadata, "initial_margin_rate": "0.99"},
    )
    bad_master = replace(
        replay.master,
        instruments={**replay.master.instruments, first_id: drifted},
    )
    with pytest.raises(ValueError, match="QExec aggregate initial margin"):
        _reporting_frames(
            artifacts=replay.artifacts,
            snapshots=replay.ledger.reporting_snapshots,
            master=bad_master,
            strategy_id="certified_spread_v1",
            money_scale=8,
        )

    maintenance_drift = replace(
        original,
        metadata={**original.metadata, "maintenance_margin_rate": "0.99"},
    )
    bad_maintenance_master = replace(
        replay.master,
        instruments={**replay.master.instruments, first_id: maintenance_drift},
    )
    with pytest.raises(ValueError, match="QExec aggregate maintenance margin"):
        _reporting_frames(
            artifacts=replay.artifacts,
            snapshots=replay.ledger.reporting_snapshots,
            master=bad_maintenance_master,
            strategy_id="certified_spread_v1",
            money_scale=8,
        )

    foreign_currency = replace(original, settlement_currency="USD")
    bad_currency_master = replace(
        replay.master,
        instruments={**replay.master.instruments, first_id: foreign_currency},
    )
    with pytest.raises(ValueError, match="settlement currency"):
        _reporting_frames(
            artifacts=replay.artifacts,
            snapshots=replay.ledger.reporting_snapshots,
            master=bad_currency_master,
            strategy_id="certified_spread_v1",
            money_scale=8,
        )


def test_three_standard_v2_outputs_have_identical_artifact_hashes(tmp_path: Path) -> None:
    completed = [
        run_certified_backtest(CONFIG, tmp_path / f"run-{index}", code_version=CODE_VERSION)
        for index in range(3)
    ]
    hashes = [
        {record.name: record.sha256 for record in item.manifest.artifacts} for item in completed
    ]
    assert hashes[0] == hashes[1] == hashes[2]


def test_certified_runner_forbids_live_floating_and_escaping_configuration(
    tmp_path: Path,
) -> None:
    for changes, message in (
        ({"mode": "live"}, "live paths are forbidden"),
        ({"certified_profile": "legacy"}, "certified_profile"),
        ({"random_seed": True}, "random_seed"),
        ({"signals": []}, "at least one"),
    ):
        with pytest.raises(ValueError, match=message):
            execute_certified_replay(_config(tmp_path, **changes))

    for path, value, message in (
        (("execution", "ledger"), "BacktestPositionBook", "QExec-only"),
        (("execution", "sends_live_orders"), True, "QExec-only"),
        (("fixture", "certification"), "unverified", "fixture-certified"),
        (("account", "base_currency"), "USD", "CNY"),
    ):
        payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        payload[path[0]][path[1]] = value
        mutated = tmp_path / f"mutated-{path[0]}-{path[1]}.yaml"
        mutated.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            execute_certified_replay(mutated)

    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["fixture"]["instrument_master"] = "../outside.json"
    escaped = tmp_path / "escaped.yaml"
    escaped.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        execute_certified_replay(escaped)


def test_fixture_master_type_is_immutable() -> None:
    master = load_fixture_master(MASTER, as_of=AS_OF)
    assert isinstance(master, FixtureMaster)
    with pytest.raises(Exception):
        master.certification = "changed"  # type: ignore[misc]
