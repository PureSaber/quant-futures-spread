"""tests/test_backtest_layout.py —— core 子包结构（无 shim）。"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"

_SHIM_NAMES = {
    "runner.py", "reconcile_sim.py", "context.py", "signal_recorder.py",
    "accounting.py", "sizing.py", "position_book.py", "market_store.py",
    "bar_converter.py", "universe.py", "config_loader.py",
    "output.py", "output_format.py", "sector_panel.py",
}


def test_subpackages_exist():
    for name in ("engine", "portfolio", "data", "io", "panel"):
        assert (CORE / name).is_dir(), f"missing core/{name}/"


def test_no_root_shims():
    for name in _SHIM_NAMES:
        assert not (CORE / name).exists(), f"shim should be removed: {name}"


def test_canonical_imports():
    from core.engine.runner import run_backtest, run_instance
    from core.io.config_loader import load_backtest_config
    from core.portfolio.accounting import SpreadAccounting

    assert callable(run_backtest)
    assert callable(run_instance)
    assert callable(load_backtest_config)
    assert SpreadAccounting is not None


def test_strategy_context_is_protocol_only():
    src = (REPO / "strategy/framework/context.py").read_text(encoding="utf-8")
    assert "from market." not in src
    assert "from trade." not in src
    assert "Protocol" in src


def test_no_strategy_imports_core():
    strategies = REPO / "strategy" / "strategies"
    for path in strategies.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "from core" not in text and "import core" not in text, path.name


def test_examples_skip_panel():
    from core.panel.sector_panel import strategy_needs_panel

    ok(not strategy_needs_panel("strategies.example_dom_sub.strategy"))
    ok(not strategy_needs_panel("strategies.example_cross_product.strategy"))


def ok(cond, msg=""):
    assert cond, msg


if __name__ == "__main__":
    test_subpackages_exist()
    test_no_root_shims()
    test_canonical_imports()
    test_strategy_context_is_protocol_only()
    test_no_strategy_imports_core()
    test_examples_skip_panel()
    print("test_backtest_layout: OK")
