"""Independent fixture-certified runner using QDK, QExec and QLab frozen releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from quant_data_kit import FixedPoint
from quant_execution import (
    BarMatchingModel,
    DeterministicBroker,
    DeterministicRunEngine,
    OrderStatus,
    RuleBookRiskGate,
    RunArtifacts,
    RunResult,
)
from quant_lab.contracts_v2 import RunManifestV2

from qfs_certified.events import EventFixture, load_event_fixture
from qfs_certified.ledger import SnapshotRecordingLedger
from qfs_certified.reference import FixtureMaster, load_fixture_master, parse_utc
from qfs_certified.standard_v2 import write_certified_standard_v2
from qfs_certified.strategy import AuditedSpreadStrategy, SpreadSignal, validate_signals

REPO_ROOT = Path(__file__).resolve().parents[1]
CERTIFIED_PROFILE = "qexec-fixture-v1"
CERTIFIED_EXECUTION = {
    "authority": "quant-execution-v0.5.1",
    "engine": "DeterministicRunEngine",
    "risk_gate": "RuleBookRiskGate",
    "matching": "BarMatchingModel",
    "ledger": "ExactAccountLedger",
    "legacy_accounting": "forbidden",
    "sends_live_orders": False,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_code_version() -> str:
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True
    ).strip()
    if status:
        raise ValueError("certified code_version requires a clean repository")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _resolve_repo_path(value: str) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"certified fixture path escapes the repository: {value}") from exc
    return resolved


def _load_config(path: str | Path) -> tuple[dict[str, Any], Path]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = (REPO_ROOT / config_path).resolve()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("certified config must be a mapping")
    if payload.get("mode") != "backtest":
        raise ValueError("certified runner supports backtest mode only; live paths are forbidden")
    if payload.get("certified_profile") != CERTIFIED_PROFILE:
        raise ValueError(f"certified_profile must be {CERTIFIED_PROFILE}")
    if not isinstance(payload.get("run_id"), str) or not payload["run_id"].strip():
        raise ValueError("run_id is required")
    seed = payload.get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("random_seed must be a non-negative integer")
    if payload.get("execution") != CERTIFIED_EXECUTION:
        raise ValueError("execution must exactly declare the frozen QExec-only certified chain")
    fixture = payload.get("fixture") or {}
    if not isinstance(fixture, dict):
        raise ValueError("fixture must be a mapping")
    if fixture.get("certification") != "fixture-certified":
        raise ValueError("fixture.certification must be fixture-certified")
    account = payload.get("account") or {}
    if not isinstance(account, dict):
        raise ValueError("account must be a mapping")
    if account.get("base_currency") != "CNY":
        raise ValueError("certified domestic-futures account base_currency must be CNY")
    if not isinstance(account.get("account_id"), str) or not account["account_id"].strip():
        raise ValueError("account.account_id is required")
    if not isinstance(payload.get("strategy_id"), str) or not payload["strategy_id"].strip():
        raise ValueError("strategy_id is required")
    parse_utc(str(payload.get("created_at", "")), "created_at")
    return payload, config_path


def _input_state(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    result = {}
    for name, path in paths.items():
        before = path.stat()
        digest = _sha256(path)
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError(f"fixture input changed while reading: {name}")
        result[name] = {"sha256": digest, "mtime_ns": after.st_mtime_ns}
    return result


@dataclass(frozen=True)
class CertifiedInputs:
    config: dict[str, Any]
    config_path: Path
    master_path: Path
    events_path: Path
    master: FixtureMaster
    event_fixture: EventFixture
    signals: tuple[SpreadSignal, ...]
    initial_cash: FixedPoint
    money_scale: int
    input_state: dict[str, dict[str, Any]]


def prepare_certified_inputs(config_path: str | Path) -> CertifiedInputs:
    """Load and validate static inputs without strategy, engine or account state."""
    path = Path(config_path)
    path = (path if path.is_absolute() else REPO_ROOT / path).resolve()
    config_before = _input_state({"config": path})["config"]
    config, resolved_config = _load_config(path)
    fixture = config["fixture"]
    master_path = _resolve_repo_path(str(fixture.get("instrument_master", "")))
    events_path = _resolve_repo_path(str(fixture.get("market_events", "")))
    paths = {"config": path, "instrument_master": master_path, "market_events": events_path}
    before = _input_state(paths)
    if config_before != before["config"]:
        raise ValueError("fixture config changed while loading")
    as_of = parse_utc(str(fixture.get("as_of", "")), "fixture.as_of")
    master = load_fixture_master(master_path, as_of=as_of)
    event_fixture = load_event_fixture(events_path, master=master)
    source = str(fixture.get("source", "qfs-local-sample-v1"))
    symbol_map = {
        mapping.provider_symbol: master.resolve(source, mapping.provider_symbol, as_of)
        for mapping in master.mappings
        if mapping.source == source
    }
    signal_payloads = config.get("signals", [])
    if not isinstance(signal_payloads, list) or any(
        not isinstance(item, dict) for item in signal_payloads
    ):
        raise ValueError("signals must be a list of mappings")
    signals = tuple(
        SpreadSignal.from_config(item, symbol_map=symbol_map) for item in signal_payloads
    )
    if not signals:
        raise ValueError("certified strategy requires at least one spread signal")
    validate_signals(signals)
    event_ids = {event.event_id for event in event_fixture.events}
    missing = [signal.signal_id for signal in signals if signal.trigger_event_id not in event_ids]
    if missing:
        raise ValueError(f"spread signal trigger is absent from fixture: {missing}")
    account = config["account"]
    money_scale = account.get("money_scale", 8)
    if isinstance(money_scale, bool) or not isinstance(money_scale, int) or money_scale < 0:
        raise ValueError("account.money_scale must be a non-negative integer")
    try:
        amount = Decimal(str(account.get("initial_cash", "")))
    except InvalidOperation as exc:
        raise ValueError("account.initial_cash must be finite and positive") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("account.initial_cash must be finite and positive")
    cash = FixedPoint.from_decimal(amount, money_scale)
    if not cash.is_positive():
        raise ValueError("account.initial_cash must be positive at money_scale")
    if _input_state(paths) != before:
        raise ValueError("fixture inputs changed during validation")
    return CertifiedInputs(
        config,
        resolved_config,
        master_path,
        events_path,
        master,
        event_fixture,
        signals,
        cash,
        money_scale,
        before,
    )


@dataclass(frozen=True)
class CertifiedReplay:
    result: RunResult
    artifacts: RunArtifacts
    master: FixtureMaster
    event_fixture: EventFixture
    strategy: AuditedSpreadStrategy
    ledger: SnapshotRecordingLedger
    config: dict[str, Any]
    config_path: Path
    master_path: Path
    events_path: Path
    input_state: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class CertifiedRun:
    replay: CertifiedReplay
    manifest: RunManifestV2
    run_dir: Path


def _assert_complete_spread_executions(
    strategy: AuditedSpreadStrategy,
    artifacts: RunArtifacts,
) -> None:
    orders_by_key = {order.intent.idempotency_key: order for order in artifacts.orders}
    if len(orders_by_key) != len(artifacts.orders):
        raise ValueError("spread replay produced duplicate order idempotency keys")
    latest_reasons = {
        event.order_id: event.reason for event in artifacts.order_events if event.reason
    }
    audits_by_signal: dict[str, list] = {}
    for audit in strategy.audit_trail:
        audits_by_signal.setdefault(audit.signal_id, []).append(audit)
    missing_signals = set(strategy.signal_ids) - set(audits_by_signal)
    if missing_signals:
        raise ValueError(
            "spread signal execution missing; "
            f"signal_ids={sorted(missing_signals)}; order_count={len(artifacts.orders)}"
        )
    audited_keys = {
        audit.idempotency_key for audits in audits_by_signal.values() for audit in audits
    }
    unexpected_keys = set(orders_by_key) - audited_keys
    if unexpected_keys:
        raise ValueError(
            f"spread replay produced orders without signal audit: {sorted(unexpected_keys)}"
        )
    for signal_id, audits in audits_by_signal.items():
        roles = {audit.leg_role for audit in audits}
        if len(audits) != 2 or roles != {"leg-a", "leg-b"}:
            raise ValueError(
                f"spread signal audit is incomplete; signal_id={signal_id}; roles={sorted(roles)}"
            )
        outcomes: list[str] = []
        complete = True
        for audit in sorted(audits, key=lambda item: item.leg_role):
            order = orders_by_key.get(audit.idempotency_key)
            if order is None:
                complete = False
                outcomes.append(f"{audit.leg_role}=missing")
                continue
            leg_complete = (
                order.status is OrderStatus.FILLED and order.filled_quantity == audit.quantity
            )
            complete = complete and leg_complete
            reason = latest_reasons.get(order.order_id, "")
            detail = (
                f"{audit.leg_role}={order.status.value}:"
                f"{order.filled_quantity.to_decimal()}/{audit.quantity.to_decimal()}"
            )
            outcomes.append(f"{detail}:{reason}" if reason else detail)
        if not complete:
            raise ValueError(
                "spread pair execution incomplete; "
                f"signal_id={signal_id}; action={audits[0].action}; " + "; ".join(outcomes)
            )


def execute_certified_replay(config_path: str | Path) -> CertifiedReplay:
    prepared = prepare_certified_inputs(config_path)
    config = prepared.config
    master = prepared.master
    event_fixture = prepared.event_fixture
    strategy = AuditedSpreadStrategy(prepared.signals)
    account = config["account"]
    account_id = str(account.get("account_id", ""))
    base_currency = str(account.get("base_currency", ""))
    money_scale = prepared.money_scale
    initial_cash = {base_currency: prepared.initial_cash}
    ledger = SnapshotRecordingLedger(
        account_id=account_id,
        base_currency=base_currency,
        instruments=master.instruments,
        initial_cash=initial_cash,
        money_scale=money_scale,
    )
    strategy_id = str(config.get("strategy_id", ""))
    engine = DeterministicRunEngine(
        run_id=str(config["run_id"]),
        account_id=account_id,
        strategy_id=strategy_id,
        strategy=strategy,
        broker=DeterministicBroker(),
        risk_gate=RuleBookRiskGate(
            instruments=master.instruments,
            ledger=ledger,
            money_scale=money_scale,
        ),
        matching_model=BarMatchingModel(
            master.instruments,
            participation_rate="1",
            slippage_ticks=0,
        ),
        ledger=ledger,
    )
    result = engine.replay(event_fixture.events, int(config["random_seed"]))
    if engine.artifacts is None:
        raise RuntimeError("QExec replay completed without RunArtifacts")
    _assert_complete_spread_executions(strategy, engine.artifacts)
    return CertifiedReplay(
        result=result,
        artifacts=engine.artifacts,
        master=master,
        event_fixture=event_fixture,
        strategy=strategy,
        ledger=ledger,
        config=config,
        config_path=prepared.config_path,
        master_path=prepared.master_path,
        events_path=prepared.events_path,
        input_state=prepared.input_state,
    )


def run_certified_backtest(
    config_path: str | Path,
    output_root: str | Path,
    *,
    code_version: str | None = None,
) -> CertifiedRun:
    replay = execute_certified_replay(config_path)
    run_dir = Path(output_root) / replay.result.run_id
    paths = {
        "config": replay.config_path,
        "instrument_master": replay.master_path,
        "market_events": replay.events_path,
    }
    if _input_state(paths) != replay.input_state:
        raise ValueError("fixture inputs changed during replay")
    master_hash = replay.input_state["instrument_master"]["sha256"]
    manifest = write_certified_standard_v2(
        run_dir,
        artifacts=replay.artifacts,
        snapshots=replay.ledger.reporting_snapshots,
        master=replay.master,
        strategy_id=str(replay.config["strategy_id"]),
        config=replay.config,
        code_version=code_version or _repository_code_version(),
        dataset_snapshots={
            "instrument_master": f"sha256:{master_hash}",
            "market_events": f"sha256:{replay.input_state['market_events']['sha256']}",
            "signal_plan": f"sha256:{replay.input_state['config']['sha256']}",
        },
        instrument_master_version=f"{replay.master.schema_version}@sha256:{master_hash}",
        random_seed=int(replay.config["random_seed"]),
        created_at=str(replay.config["created_at"]),
        money_scale=int(replay.config["account"].get("money_scale", 8)),
    )
    return CertifiedRun(replay=replay, manifest=manifest, run_dir=run_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-root")
    parser.add_argument("--preflight", action="store_true", help="Read-only fixture/config check")
    args = parser.parse_args(argv)
    if args.preflight:
        if args.output_root is not None:
            parser.error("--preflight does not accept --output-root")
        from qfs_certified.preflight import preflight

        print(json.dumps(preflight(args.config), ensure_ascii=False, sort_keys=True))
        return 0
    if args.output_root is None:
        parser.error("--output-root is required unless --preflight is selected")
    completed = run_certified_backtest(args.config, args.output_root)
    print(
        json.dumps(
            {
                "run_dir": str(completed.run_dir),
                "result_sha256": completed.replay.result.result_sha256,
                "orders": completed.replay.result.order_count,
                "fills": completed.replay.result.fill_count,
                "profile": completed.manifest.profile,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
