"""A repeated run ID must never reuse a previous invocation's market panel."""

from types import SimpleNamespace
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

from core.engine import runner
from utils import panel_registry


def test_same_run_id_rebuilds_after_input_changes(monkeypatch):
    panel_registry.clear_panels()
    built = []
    cfg = SimpleNamespace(run_id="backtest", is_calendar_mode=lambda: False)
    source = {"price": 100}
    monkeypatch.setattr(runner, "strategy_needs_panel", lambda _: True)
    monkeypatch.setattr(runner, "_sector_maps", lambda *_: ({}, {}))

    def build(*args, **kwargs):
        panel = SimpleNamespace(price=source["price"])
        built.append(panel)
        return panel

    monkeypatch.setattr(runner, "build_panel_from_entries", build)
    entries = [{"module": "sector"}]
    runner._maybe_build_sector_panel(entries, cfg, None, source, None)
    source["price"] = 200
    runner._maybe_build_sector_panel(entries, cfg, None, source, None)
    assert len(built) == 2
    assert panel_registry.get_panel("backtest").price == 200


def test_run_without_panel_does_not_retain_previous_panel(monkeypatch):
    cfg = SimpleNamespace(run_id="backtest")
    panel_registry.register_panel(cfg.run_id, SimpleNamespace(price=100))
    monkeypatch.setattr(runner, "strategy_needs_panel", lambda _: False)
    runner._maybe_build_sector_panel([{"module": "plain"}], cfg, None, None, None)
    assert panel_registry.get_panel(cfg.run_id) is None


def test_worker_receives_current_parent_panel(monkeypatch):
    panel_registry.register_panel("backtest", SimpleNamespace(price=100))
    current = SimpleNamespace(price=200)
    cfg = SimpleNamespace(
        run_id="backtest", future_list_path="unused", source_type="csv", data_dir="unused"
    )
    monkeypatch.setattr(runner, "bootstrap_strategy_path", lambda: None)
    monkeypatch.setattr(runner.FutureList, "load", lambda _: None)
    monkeypatch.setattr(runner, "build_source", lambda *_: None)
    runner._init_worker(cfg, None, current)
    assert panel_registry.get_panel("backtest") is current


def test_registry_does_not_reload_disk_after_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(panel_registry, "_CACHE_DIR", tmp_path, raising=False)
    (tmp_path / "backtest.pkl").write_bytes(b"untrusted legacy pickle")
    panel_registry.register_panel("backtest", SimpleNamespace(price=100))
    panel_registry.clear_panels()
    assert panel_registry.get_panel("backtest") is None
    assert (tmp_path / "backtest.pkl").read_bytes() == b"untrusted legacy pickle"


def test_spawned_worker_uses_supplied_panel_without_disk_cache(tmp_path):
    cfg = SimpleNamespace(
        run_id="backtest",
        future_list_path=str(Path(__file__).parents[1] / "config/future_list.csv"),
        source_type="csv_catalog",
        data_dir=str(tmp_path),
    )
    panel_registry.register_panel("backtest", SimpleNamespace(price=100))
    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=runner._init_worker,
        initargs=(cfg, None, SimpleNamespace(price=200)),
    ) as pool:
        assert pool.submit(panel_registry.get_panel, "backtest").result(timeout=30).price == 200
    assert panel_registry.get_panel("backtest").price == 100
