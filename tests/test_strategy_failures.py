from datetime import datetime
from pathlib import Path
import sys

import pytest

from core.engine import runner
from framework.base import OPEN_LONG, Strategy, TargetOrder
from test_backtest_runner import _make_two_instance_cfg, _write_spread_csv


@pytest.mark.parametrize("callback", ["on_bar", "on_trade"])
def test_manual_cli_aborts_without_success_outputs(tmp_path, monkeypatch, capsys, callback):
    import run_backtest
    from strategies.example_cross_product import strategy
    from core.io import config_loader

    _write_spread_csv(tmp_path, "A2003&A2005")
    cfg = _make_two_instance_cfg(tmp_path, jobs=1)
    cfg.strategies = cfg.strategies[:1]
    calls = []

    def fail(self, event):
        calls.append(event)
        raise RuntimeError("injected strategy failure")

    monkeypatch.setattr(
        strategy.Strategy,
        "on_bar",
        lambda self, bar: [TargetOrder(bar.symbol, OPEN_LONG, bar.close_price, 1)],
    )
    monkeypatch.setattr(strategy.Strategy, callback, fail)
    monkeypatch.setattr(config_loader, "load_backtest_config", lambda *args: cfg)
    monkeypatch.setattr(sys, "argv", ["qfs-backtest", "--research-only", "--config", "test.yaml"])
    with pytest.raises(RuntimeError, match="injected strategy failure"):
        run_backtest.main()
    assert len(calls) == 1
    assert "[done]" not in capsys.readouterr().out
    assert not Path(cfg.output_dir).exists()


@pytest.mark.parametrize("callback", ["on_bar", "on_trade"])
def test_calendar_bar_loop_propagates_callback_errors(callback):
    symbol = "A2003&A2005"
    market = runner.InMemoryMarketStore()
    book = runner.BacktestPositionBook()
    ctx = runner.BacktestStrategyContext("failure", market, book, {}, {})
    strategy = Strategy("failure", {"symbol": symbol}, ctx)
    strategy.on_bar = lambda bar: [TargetOrder(symbol, OPEN_LONG, 100.0, 1)]

    def fail(event):
        raise RuntimeError("injected calendar failure")

    setattr(strategy, callback, fail)
    bar = runner.BarData(
        symbol=symbol,
        exchange="DCE",
        product_id="A",
        trading_day="2020-01-02",
        datetime=datetime(2020, 1, 2, 9),
        open_price=100,
        high_price=101,
        low_price=99,
        close_price=100,
        leg_close_x=3000,
        leg_close_y=2900,
    )
    fl = runner.FutureList.load(str(Path(__file__).resolve().parents[1] / "config/future_list.csv"))
    with pytest.raises(RuntimeError, match="injected calendar failure"):
        runner._run_bar_loop(
            "failure",
            strategy,
            runner.ReconcileSimulator(),
            book,
            runner.SpreadAccounting(10, 100000),
            market,
            [bar],
            symbol,
            fl,
            runner.SpreadOpenDayTracker(),
            runner.RollGovernor(trade_symbol=symbol),
            {bar.trading_day: symbol},
            runner.SignalRecorder("failure"),
        )
