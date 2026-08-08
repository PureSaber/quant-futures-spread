"""Wave 2: backtest/ is a compatibility shim onto core/."""
from __future__ import annotations


def test_backtest_runner_reexports_core_identity() -> None:
    from backtest.engine import runner as bt_runner
    from core.engine import runner as core_runner

    assert bt_runner.run_backtest is core_runner.run_backtest
    assert bt_runner.run_instance is core_runner.run_instance
    assert bt_runner.run_backtest.__module__ == "core.engine.runner"


def test_backtest_sector_panel_reexports_core() -> None:
    from backtest.panel import sector_panel as bt_panel
    from core.panel import sector_panel as core_panel

    assert bt_panel.SpreadSectorPanel is core_panel.SpreadSectorPanel
    assert bt_panel.build_panel_from_schedule is core_panel.build_panel_from_schedule


def test_backtest_roll_lives_in_core() -> None:
    from backtest.data.roll import gen_trade_symbol_list, iter_rolls
    from core.data.roll import gen_trade_symbol_list as core_gen
    from core.data.roll import iter_rolls as core_iter

    assert gen_trade_symbol_list is core_gen
    assert iter_rolls is core_iter
