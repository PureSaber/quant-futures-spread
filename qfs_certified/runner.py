"""Independent fixture-certified runner using QDK, QExec and QLab frozen releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from quant_data_kit import FixedPoint
from quant_execution import (
    BarMatchingModel,
    DeterministicBroker,
    DeterministicRunEngine,
    RuleBookRiskGate,
    RunArtifacts,
    RunResult,
)
from quant_lab.contracts_v2 import RunManifestV2

from qfs_certified.events import EventFixture, load_event_fixture
from qfs_certified.ledger import SnapshotRecordingLedger
from qfs_certified.reference import FixtureMaster, load_fixture_master, parse_utc
from qfs_certified.standard_v2 import write_certified_standard_v2
from qfs_certified.strategy import AuditedSpreadStrategy, SpreadSignal

REPO_ROOT = Path(__file__).resolve().parents[1]
CERTIFIED_PROFILE = "qexec-fixture-v1"
CERTIFIED_EXECUTION = {
    "authority": "quant-execution-v0.3.0",
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
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if payload.get("mode") != "backtest":
        raise ValueError("certified runner supports backtest mode only; live paths are forbidden")
    if payload.get("certified_profile") != CERTIFIED_PROFILE:
        raise ValueError(f"certified_profile must be {CERTIFIED_PROFILE}")
    if not str(payload.get("run_id", "")).strip():
        raise ValueError("run_id is required")
    seed = payload.get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("random_seed must be a non-negative integer")
    if payload.get("execution") != CERTIFIED_EXECUTION:
        raise ValueError("execution must exactly declare the frozen QExec-only certified chain")
    fixture = payload.get("fixture") or {}
    if fixture.get("certification") != "fixture-certified":
        raise ValueError("fixture.certification must be fixture-certified")
    account = payload.get("account") or {}
    if account.get("base_currency") != "CNY":
        raise ValueError("certified domestic-futures account base_currency must be CNY")
    if not str(account.get("account_id", "")).strip():
        raise ValueError("account.account_id is required")
    if not str(payload.get("strategy_id", "")).strip():
        raise ValueError("strategy_id is required")
    parse_utc(str(payload.get("created_at", "")), "created_at")
    return payload, config_path


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


@dataclass(frozen=True)
class CertifiedRun:
    replay: CertifiedReplay
    manifest: RunManifestV2
    run_dir: Path


def execute_certified_replay(config_path: str | Path) -> CertifiedReplay:
    config, resolved_config = _load_config(config_path)
    fixture = config.get("fixture") or {}
    master_path = _resolve_repo_path(str(fixture.get("instrument_master", "")))
    events_path = _resolve_repo_path(str(fixture.get("market_events", "")))
    as_of = parse_utc(str(fixture.get("as_of", "")), "fixture.as_of")
    master = load_fixture_master(master_path, as_of=as_of)
    event_fixture = load_event_fixture(events_path, master=master)
    source = str(fixture.get("source", "qfs-local-sample-v1"))
    symbol_map = {
        mapping.provider_symbol: master.resolve(source, mapping.provider_symbol, as_of)
        for mapping in master.mappings
        if mapping.source == source
    }
    signals = tuple(
        SpreadSignal.from_config(item, symbol_map=symbol_map) for item in config.get("signals", [])
    )
    if not signals:
        raise ValueError("certified strategy requires at least one spread signal")
    strategy = AuditedSpreadStrategy(signals)

    account = config.get("account") or {}
    account_id = str(account.get("account_id", ""))
    base_currency = str(account.get("base_currency", ""))
    money_scale = int(account.get("money_scale", 8))
    initial_cash = {
        base_currency: FixedPoint.from_decimal(str(account["initial_cash"]), money_scale)
    }
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
    return CertifiedReplay(
        result=result,
        artifacts=engine.artifacts,
        master=master,
        event_fixture=event_fixture,
        strategy=strategy,
        ledger=ledger,
        config=config,
        config_path=resolved_config,
        master_path=master_path,
        events_path=events_path,
    )


def run_certified_backtest(
    config_path: str | Path,
    output_root: str | Path,
    *,
    code_version: str | None = None,
) -> CertifiedRun:
    replay = execute_certified_replay(config_path)
    run_dir = Path(output_root) / replay.result.run_id
    master_hash = _sha256(replay.master_path)
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
            "market_events": f"sha256:{_sha256(replay.events_path)}",
            "signal_plan": f"sha256:{_sha256(replay.config_path)}",
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
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args(argv)
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
