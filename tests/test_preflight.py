from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from qfs_certified import runner
from qfs_certified.preflight import preflight

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/certified_local_sample_v1.yaml"


def _state(root):
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "source"
    fixture_dir = root / "data/local_sample/certified_v1"
    shutil.copytree(ROOT / "data/local_sample/certified_v1", fixture_dir)
    config = root / "config.yaml"
    shutil.copyfile(CONFIG, config)
    monkeypatch.setattr(runner, "REPO_ROOT", root)
    return root, config


def _forbid(*args, **kwargs):
    raise AssertionError("preflight must not create strategy/execution/account state")


def test_cli_preflight_is_read_only_without_strategy_or_ledger(isolated, monkeypatch, capsys):
    root, config = isolated
    before = _state(root)
    for name in ("AuditedSpreadStrategy", "SnapshotRecordingLedger", "DeterministicRunEngine"):
        monkeypatch.setattr(runner, name, _forbid)
    assert runner.main(["--config", str(config), "--preflight"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["read_only"] and not report["investable"]
    assert report["evidence_kind"] == "synthetic"
    assert (report["event_count"], report["signal_count"]) == (23, 4)
    assert report["initial_cash"] == "200000.00000000"
    assert len(report["inputs"]) == 3
    assert _state(root) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_cash", "NaN"),
        ("initial_cash", "Infinity"),
        ("initial_cash", "0"),
        ("initial_cash", "-1"),
        ("initial_cash", "invalid"),
        ("money_scale", True),
        ("money_scale", 1.5),
        ("money_scale", -1),
    ],
)
def test_invalid_account_is_rejected_before_execution(isolated, monkeypatch, field, value):
    root, config = isolated
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    payload["account"][field] = value
    config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setattr(runner, "SnapshotRecordingLedger", _forbid)
    for check in (preflight, runner.execute_certified_replay):
        with pytest.raises(ValueError, match=field):
            check(config)
    assert not (root / "output").exists()


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("trigger", "trigger is absent"),
        ("duplicate", "must be unique"),
        ("signals", "list of mappings"),
        ("account", "account must be a mapping"),
        ("fixture", "fixture must be a mapping"),
        ("root", "config must be a mapping"),
    ],
)
def test_shared_static_validation(isolated, monkeypatch, mutation, expected):
    _, config = isolated
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    if mutation == "trigger":
        payload["signals"][0]["trigger_event_id"] = "absent"
    elif mutation == "duplicate":
        payload["signals"].append(payload["signals"][0].copy())
    elif mutation == "root":
        payload = [1]
    else:
        payload[mutation] = "invalid"
    config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setattr(runner, "SnapshotRecordingLedger", _forbid)
    for check in (preflight, runner.execute_certified_replay):
        with pytest.raises(ValueError, match=expected):
            check(config)


@pytest.mark.parametrize(
    "changed", ["config.yaml", "data/local_sample/certified_v1/market_events.json"]
)
def test_detects_input_changes_during_load(isolated, monkeypatch, changed):
    root, config = isolated
    original = runner.load_event_fixture

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        with (root / changed).open("a", encoding="utf-8") as stream:
            stream.write("\n")
        return result

    monkeypatch.setattr(runner, "load_event_fixture", changing)
    with pytest.raises(ValueError, match="changed during validation"):
        preflight(config)


def test_preflight_does_not_replace_replay_or_lock_later_inputs(isolated):
    root, config = isolated
    checked = preflight(config)
    replay = runner.execute_certified_replay(config)
    assert checked["inputs"] == replay.input_state
    assert replay.result.order_count == replay.result.fill_count == 8
    events = root / "data/local_sample/certified_v1/market_events.json"
    events.write_text("{}", encoding="utf-8")
    for check in (preflight, runner.execute_certified_replay):
        with pytest.raises(ValueError, match="event fixture schema"):
            check(config)


def test_change_during_replay_cannot_be_certified(isolated, monkeypatch):
    root, config = isolated
    original = runner.execute_certified_replay

    def changing(path):
        replay = original(path)
        with config.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        return replay

    monkeypatch.setattr(runner, "execute_certified_replay", changing)
    with pytest.raises(ValueError, match="changed during replay"):
        runner.run_certified_backtest(config, root / "output", code_version="a" * 40)
    assert not (root / "output").exists()


def test_cli_rejects_output_in_preflight(isolated):
    root, config = isolated
    with pytest.raises(SystemExit) as exc:
        runner.main(["--config", str(config), "--preflight", "--output-root", str(root / "output")])
    assert exc.value.code == 2
    assert not (root / "output").exists()
    with pytest.raises(SystemExit):
        runner.main(["--config", str(config)])
