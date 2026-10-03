from __future__ import annotations

from io import StringIO

import pandas as pd
import pytest

from core.data.bar_converter import df_to_bars


def convert(frame):
    return df_to_bars(frame, "RB2610&RB2701", "SHFE")


def frame():
    return pd.DataFrame({"datetime": ["2026-09-18"], "close": [-10.0]})


@pytest.mark.parametrize("column", ["close", "open", "high", "low", "bidPrice", "askPrice", "volume", "oi", "close_x"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "bad"])
def test_invalid_numeric_input_is_rejected(column, value):
    data = frame()
    data[column] = value
    with pytest.raises(ValueError, match=column):
        convert(data)


@pytest.mark.parametrize("value", [False, "False", "False ", " false ", "0", 0, 0.0])
def test_false_trade_flags_remain_false(value):
    data = frame()
    data["trade"] = value
    assert convert(data)[0].tradable is False


@pytest.mark.parametrize("value", [True, "True", " true ", "1", 1, 1.0])
def test_true_trade_flags_remain_true(value):
    data = frame()
    data["trade"] = value
    assert convert(data)[0].tradable is True


@pytest.mark.parametrize("value", [None, float("nan"), "", "unknown", "2", 2, -1])
def test_invalid_trade_flags_are_rejected(value):
    data = frame()
    data["trade"] = value
    with pytest.raises(ValueError, match="trade"):
        convert(data)


def test_csv_false_with_whitespace_is_not_tradable():
    from utils.strategy_bootstrap import bootstrap_strategy_path

    bootstrap_strategy_path()
    from core.engine.reconcile_sim import ReconcileSimulator
    from framework.base import OPEN_LONG, TargetOrder

    data = pd.read_csv(StringIO("datetime,close,trade\n2026-09-18,-10,False \n"))
    bar = convert(data)[0]
    assert bar.tradable is False
    sim = ReconcileSimulator()
    sim.reconcile("test", bar.symbol, [TargetOrder(bar.symbol, OPEN_LONG, -10, 1)], 1)
    assert sim.try_fill(bar) == []


@pytest.mark.parametrize("value", [-10.0, 0.0, 10.0])
def test_signed_spreads_and_absent_optional_columns_are_supported(value):
    data = frame()
    data["close"] = value
    bar = convert(data)[0]
    assert bar.close_price == bar.open_price == bar.high_price == bar.low_price == value
    assert bar.tradable is True
    assert bar.volume == 0


@pytest.mark.parametrize("column", ["datetime", "tradingday"])
def test_missing_time_is_rejected(column):
    data = frame()
    data[column] = None
    with pytest.raises(ValueError, match=column):
        convert(data)


def test_duplicate_and_reversed_times_are_rejected():
    data = pd.concat([frame(), frame()], ignore_index=True)
    with pytest.raises(ValueError, match="datetime"):
        convert(data)
    data["datetime"] = ["2026-09-18", "2026-09-17"]
    with pytest.raises(ValueError, match="datetime"):
        convert(data)
