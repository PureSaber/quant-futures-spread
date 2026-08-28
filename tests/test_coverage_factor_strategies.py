from __future__ import annotations

import math
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from framework.base import CLOSE_LONG, CLOSE_SHORT, OPEN_LONG, OPEN_SHORT
from framework.incremental import (
    IncrementalRollingMean,
    IncrementalRollingQuantile,
    IncrementalRollingStd,
)
from strategies.factor_combo.strategy import Strategy as ComboStrategy
from strategies.factor_single import factors
from strategies.factor_single.strategy import Strategy as SingleStrategy


def _bars(count: int = 300) -> list[dict]:
    start = datetime(2020, 1, 1, 9, 0)
    rows = []
    for index in range(count):
        close = 100 + index * 0.1 + (index % 7) * 0.2
        rows.append(
            {
                "datetime": start + timedelta(days=index),
                "close_price": close,
                "open_price": close - 0.1,
                "high_price": close + 1,
                "low_price": close - 1,
                "bid_price": close - 0.2,
                "ask_price": close + 0.2,
                "bid_low": close - 0.3,
                "ask_high": close + 0.3,
                "bid_volume": 10 + index % 3,
                "ask_volume": 8 + index % 5,
                "leg_close_x": 3000 + index,
                "leg_close_y": 2900 + index * 0.8,
                "close_20": close - 2,
                "close_80": close + 2,
                "bid_price_20": close - 1,
                "bid_price_80": close + 1,
                "ask_price_20": close - 1,
                "ask_price_80": close + 1,
            }
        )
    return rows


def test_factor_helpers_cover_valid_invalid_and_degenerate_inputs() -> None:
    assert factors._bar_field(None, "x", 3) == 3
    assert factors._bar_field({"x": "2"}, "x") == 2
    assert factors._bar_field(SimpleNamespace(x="bad"), "x", 4) == 4
    assert factors._contract_yyyymm("A2405", 2024) == 202405
    assert factors._contract_yyyymm("A905", 2019) == 201905
    assert factors._contract_yyyymm("bad", 2024) is None
    assert factors._contract_yyyymm("A12", 2024) is None
    assert factors._contract_yyyymm("A2413", 2024) is None
    assert factors._days_between(202405, 202409) > 0
    assert factors._days_between(202409, 202405) is None
    assert factors._days_between(202413, 202501) is None
    assert factors._zscore([1], 2) is None
    assert factors._zscore([1, 1], 2) == 0
    assert factors._zscore([1, 2], 2) > 0
    assert math.isnan(factors._quantile_sorted([], 0.5))
    assert factors._quantile_sorted([1], 1) == 1
    assert factors._quantile_sorted([1, 3], 0.5) == 2
    assert factors._percentile_rank([1], 2) is None
    assert factors._percentile_rank([1, 2, 2], 3) == pytest.approx(2 / 3)
    assert factors._band_position(2, 1, 1) == 0.5
    assert factors._band_position(2, 1, 3) == 0.5
    assert factors._mid_dev_series([{"close_price": 2}]) == [0]
    assert factors._carry_ann(1, "A2405") is None
    assert factors._carry_ann(1, "bad&A2405") is None
    assert factors._carry_ann(1, "A2409&A2405") is None
    assert factors._carry_ann(1, "A2405&A2409") is not None
    assert factors._pct_rank([1], {}, 2) is None
    assert factors._pct_rank([1, 1], {}, 2) == 0.5
    assert factors._pct_rank([1, 2], {"close_20": 0, "close_80": 4}, 2) == 0.5
    assert factors._mom_series([1], 2) == []
    assert factors._mom_series([1, 2, 4], 1) == [1, 2]
    assert factors._leg_mom_diff_series([{"leg_close_x": 0, "leg_close_y": 0}]) == []
    assert factors._leg_mom_diff_series(_bars(20), 15)
    assert factors._range_pct_series([{"close_price": 0, "high_price": 1}]) == [0]
    assert factors._depth_imb({"bid_volume": 0, "ask_volume": 0}) is None
    assert factors._depth_imb({"bid_volume": 3, "ask_volume": 1}) == 0.25
    assert factors._depth_imb_series([{"bid_volume": 0, "ask_volume": 0}]) == [0]
    assert factors._realized_vol_series([1, 2], 2) == []
    assert factors._realized_vol_series([1, 2, 4], 2)
    assert factors._seasonal_dev([1], [{}], 2) is None
    assert factors._seasonal_dev([1, 2], [{}, {}], 2) is None
    short_seasonal = _bars(2)
    assert factors._seasonal_dev([1, 2], short_seasonal, 2) is None
    seasonal = _bars(5)
    assert factors._seasonal_dev([row["close_price"] for row in seasonal], seasonal, 5) is not None


def test_every_factor_and_entry_rank_executes_meaningful_paths() -> None:
    bars = _bars()
    bar = bars[-1]
    names = (
        "z_close",
        "mid_dev",
        "pct_rank",
        "mom_5",
        "mom_15",
        "mom_60",
        "mom_240",
        "leg_mom_diff_15",
        "carry_ann",
        "breakout_down_60",
        "breakout_up_60",
        "boll_pct_b",
        "z_bid",
        "z_ask",
        "depth_imb",
        "range_pct",
        "vol_ratio",
        "realized_vol_20",
        "realized_vol_120",
        "eff_spread",
        "quote_width",
        "seasonal_dev",
    )
    results = {
        name: factors.compute_factor_value(
            name,
            bars,
            bar,
            "A2405&A2409",
            20,
            vol_short=5,
            vol_long=10,
        )
        for name in names
    }
    assert set(results) == set(names)
    assert results["mid_dev"] == pytest.approx(0)
    assert results["eff_spread"] == pytest.approx(0.4)
    assert results["quote_width"] == pytest.approx(2)
    assert factors.compute_factor_value("unknown", bars, bar, "A&B", 20) is None
    assert factors.compute_factor_value("z_close", [], bar, "A&B", 20) is None
    assert factors.compute_factor_value("z_close", bars[:2], bar, "A&B", 20) is None

    quantile_names = (
        "mid_dev",
        "mom_5",
        "mom_15",
        "mom_60",
        "mom_240",
        "depth_imb",
        "range_pct",
        "vol_ratio",
        "leg_mom_diff_15",
    )
    for name in quantile_names:
        assert factors.compute_entry_rank(name, bars, bar, "A2405&A2409", 20) is not None
    assert factors.compute_entry_rank("unknown", bars, bar, "A&B", 20) is None
    assert factors.compute_entry_rank("mom_5", bars[:3], bar, "A&B", 20) is None
    assert factors.factor_ic_sign("depth_imb") == 1
    assert factors.factor_ic_sign("pct_rank") == -1
    assert factors.factor_uses_quantile_entry("mom_5")
    assert not factors.factor_uses_quantile_entry("z_close")
    assert factors.factor_uses_pct_band("z_bid")
    assert not factors.factor_uses_pct_band("z_close")


class Position:
    def __init__(self, long_qty=0.0, short_qty=0.0):
        self.long_qty = long_qty
        self.short_qty = short_qty


class Context:
    def __init__(self, bars=None, position=None, tick=1.0):
        self.bars = bars or _bars(30)
        self.position = position or Position()
        self.tick = tick

    def get_bars(self, *args, **kwargs):
        return self.bars

    def get_position(self, symbol):
        return self.position

    def price_tick_of(self, symbol):
        return self.tick


def _bar(ask=101.0, bid=99.0):
    return SimpleNamespace(
        close_price=100.0,
        ask_price=ask,
        bid_price=bid,
        ask_high=ask,
        bid_low=bid,
    )


def _single(**params):
    values = {
        "symbol": "A2405&A2409",
        "factor": "pct_rank",
        "lookback": 10,
        "min_hold_bars": 1,
        "horizon_min": 3,
        "cooldown_bars": 2,
        "max_eff_spread": 5,
    }
    values.update(params)
    return SingleStrategy("single", values, Context())


def test_single_strategy_all_entry_exit_and_guard_branches(monkeypatch) -> None:
    assert SingleStrategy("x", {}, Context()).on_bar(_bar()) == []
    strategy = _single()
    monkeypatch.setattr(strategy, "_signal_val", lambda bars, bar: None)
    assert strategy.on_bar(_bar()) == []
    monkeypatch.setattr(strategy, "_signal_val", lambda bars, bar: 0.1)
    assert strategy.on_bar(_bar())[0].side == OPEN_LONG
    strategy.ctx.position = Position()
    strategy._cooldown_left = 2
    assert strategy.on_bar(_bar()) == []
    assert strategy._cooldown_left == 1
    strategy._cooldown_left = 0
    assert strategy.on_bar(_bar(ask=110, bid=90)) == []

    short = _single()
    monkeypatch.setattr(short, "_signal_val", lambda bars, bar: 0.9)
    assert short.on_bar(_bar())[0].side == OPEN_SHORT
    short.trade_mode = 1
    assert short.on_bar(_bar()) == []
    strategy.trade_mode = -1
    assert strategy.on_bar(_bar()) == []

    long_exit = _single(use_conservative_fill=True)
    long_exit.ctx.position = Position(long_qty=2)
    monkeypatch.setattr(long_exit, "_signal_val", lambda bars, bar: 0.7)
    assert long_exit.on_bar(_bar())[0].side == CLOSE_LONG
    assert long_exit._cooldown_left == 2
    short_exit = _single(use_conservative_fill=True)
    short_exit.ctx.position = Position(short_qty=3)
    monkeypatch.setattr(short_exit, "_signal_val", lambda bars, bar: 0.3)
    assert short_exit.on_bar(_bar())[0].side == CLOSE_SHORT

    strategy.on_sizing([4])
    assert strategy._vol == 4
    strategy.on_sizing([])
    strategy.on_symbol_switch("old", "new")
    assert strategy.symbol == "new"
    assert strategy._read_vol(0) == 1
    assert strategy._read_vol([]) == 1
    strategy.ctx.tick = 0
    assert strategy._tick() == 0.01
    assert strategy._px(1.239, OPEN_LONG) <= 1.239
    assert strategy._px(1.231, OPEN_SHORT) >= 1.231
    assert strategy._order_price(_bar(), OPEN_LONG) == 100


def test_single_strategy_decision_helpers_cover_factor_semantics() -> None:
    strategy = _single()
    strategy._ic_sign = 1
    assert strategy._want_long(0.9)
    assert strategy._want_short(0.1)
    strategy._ic_sign = -1
    assert strategy._want_long(0.1)
    assert strategy._want_short(0.9)
    strategy.factor = "breakout_down_60"
    strategy._ic_sign = 1
    assert strategy._want_long(1)
    assert not strategy._want_short(1)
    strategy.factor = "breakout_up_60"
    assert not strategy._want_long(1)
    assert strategy._want_short(1)
    strategy.factor = "z_close"
    assert strategy._want_long(-2)
    assert strategy._want_short(2)
    strategy._bars_in_pos = 0
    assert not strategy._want_exit_long(1)
    assert not strategy._want_exit_short(-1)
    strategy._bars_in_pos = strategy.horizon_min
    assert strategy._want_exit_long(-99)
    assert strategy._want_exit_short(99)
    strategy._bars_in_pos = strategy.min_hold_bars
    strategy.factor = "pct_rank"
    strategy._ic_sign = 1
    assert strategy._want_exit_long(0.1)
    assert strategy._want_exit_short(0.9)
    strategy._ic_sign = -1
    assert strategy._want_exit_long(0.9)
    assert strategy._want_exit_short(0.1)
    strategy.factor = "breakout_down_60"
    assert strategy._want_exit_long(0)
    strategy.factor = "breakout_up_60"
    assert strategy._want_exit_long(0)
    strategy.factor = "z_close"
    assert strategy._want_exit_long(1)
    assert strategy._want_exit_short(-1)


def _combo(position=None, **params):
    values = {
        "symbol": "A2405&A2409",
        "lookback": 10,
        "min_hold_bars": 1,
        "horizon_min": 3,
        "cooldown_bars": 2,
        "max_eff_spread": 5,
    }
    values.update(params)
    return ComboStrategy("combo", values, Context(position=position))


def test_combo_strategy_entry_exit_guards_and_helpers(monkeypatch) -> None:
    assert ComboStrategy("empty", {}, Context()).on_bar(_bar()) == []
    strategy = _combo()
    monkeypatch.setattr(strategy, "_signal_val", lambda bars, bar: None)
    monkeypatch.setattr(strategy, "_gate_rank", lambda bars, bar: 0.1)
    assert strategy.on_bar(_bar()) == []
    monkeypatch.setattr(strategy, "_signal_val", lambda bars, bar: 0.1)
    assert strategy.on_bar(_bar())[0].side == OPEN_LONG
    short = _combo()
    monkeypatch.setattr(short, "_signal_val", lambda bars, bar: 0.9)
    monkeypatch.setattr(short, "_gate_rank", lambda bars, bar: 0.9)
    assert short.on_bar(_bar())[0].side == OPEN_SHORT
    strategy._cooldown_left = 2
    assert strategy.on_bar(_bar()) == []
    strategy._cooldown_left = 0
    assert strategy.on_bar(_bar(110, 90)) == []
    short.trade_mode = 1
    assert short.on_bar(_bar()) == []

    long_exit = _combo(Position(long_qty=2), use_conservative_fill=True)
    monkeypatch.setattr(long_exit, "_signal_val", lambda bars, bar: 0.9)
    monkeypatch.setattr(long_exit, "_gate_rank", lambda bars, bar: 0.5)
    assert long_exit.on_bar(_bar())[0].side == CLOSE_LONG
    short_exit = _combo(Position(short_qty=2), use_conservative_fill=True)
    monkeypatch.setattr(short_exit, "_signal_val", lambda bars, bar: 0.1)
    monkeypatch.setattr(short_exit, "_gate_rank", lambda bars, bar: 0.5)
    assert short_exit.on_bar(_bar())[0].side == CLOSE_SHORT
    strategy.on_sizing([3])
    strategy.on_sizing([])
    strategy.on_symbol_switch("old", "new")
    assert strategy._read_vol(0) == 1
    assert strategy._read_vol([]) == 1
    strategy.ctx.tick = 0
    assert strategy._tick() == 0.01
    assert strategy._px(1.239, OPEN_LONG) <= 1.239
    assert strategy._px(1.231, OPEN_SHORT) >= 1.231
    assert strategy._gate_allows_long(0.1)
    assert strategy._gate_allows_short(0.9)
    assert strategy._order_price(_bar(), CLOSE_LONG) == 100


def test_incremental_operators_match_rolling_edge_semantics() -> None:
    mean = IncrementalRollingMean(3, min_periods=2)
    values = [mean.update(value) for value in (1.0, float("nan"), 3.0, 5.0)]
    assert math.isnan(values[0])
    assert values[2:] == [2.0, 4.0]
    std = IncrementalRollingStd(3, min_periods=2)
    std_values = [std.update(value) for value in (1.0, float("nan"), 3.0, 5.0)]
    assert math.isnan(std_values[0]) and math.isnan(std_values[1])
    assert std_values[2] == pytest.approx(math.sqrt(2))
    assert std_values[3] == pytest.approx(math.sqrt(2))
    one = IncrementalRollingStd(2, min_periods=1)
    assert math.isnan(one.update(1.0))
    quantile = IncrementalRollingQuantile(3, 0.5, min_periods=1)
    assert quantile.update(1.0) == 1
    assert quantile.update(3.0) == 2
    assert quantile.update(float("nan")) == 2
    assert quantile.update(5.0) == 4
    upper = IncrementalRollingQuantile(2, 1.0, min_periods=2)
    assert math.isnan(upper.update(1.0))
    assert upper.update(2.0) == 2
