from __future__ import annotations

import pickle
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.panel.calendar_panel import build_product_daily_close, calendar_board_peers
from core.panel.sector_panel import (
    SpreadSectorPanel,
    _daily_last_close,
    _normalize_minute_df,
    build_panel_from_entries,
    build_panel_from_schedule,
    build_sector_extreme_cache,
    strategy_needs_panel,
)
from research.common.loader import add_forward_labels, apply_filters, load_spread_csv
from research.eval.ic_analysis import (
    _daily_rank_ic,
    _label_col,
    _rank_corr,
    compare_ic_summaries,
    compute_ic_panel,
    filter_redundant_factors,
    ic_by_sector,
    quantile_spread_test,
    summarize_ic,
)
from utils import panel_registry
from utils.calendar_peers import active_spreads_on_day
from utils.dominant_contract import (
    contract_yyyymm,
    normalize_tenor,
    product_of,
    spread_from_legs,
)
from utils.spread_sector import (
    board_of_product,
    build_board_peers,
    leg0_product,
    sector_ext_at,
    tradingday_i64,
)
from utils.ta import Boll, Boll3, round_down, round_up


def _minute_frame(symbol_shift: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": ["2020-01-02 09:00", "2020-01-02 09:01", "2020-01-03 09:00"],
            "tradingday": ["2020-01-02", "2020-01-02", "2020-01-03"],
            "open": [1, "2", 3],
            "high": [2, 3, 4],
            "low": [0, 1, 2],
            "close": [10 + symbol_shift, 11 + symbol_shift, 12 + symbol_shift],
        }
    )


def test_minute_normalization_daily_close_and_sector_extremes() -> None:
    assert _normalize_minute_df(None).empty
    assert _normalize_minute_df(pd.DataFrame()).empty
    normalized = _normalize_minute_df(_minute_frame())
    assert isinstance(normalized.index, pd.DatetimeIndex)
    assert normalized["volume"].eq(0).all()
    assert normalized.loc[pd.Timestamp("2020-01-02 09:01"), "open"] == 2

    indexed = (
        _minute_frame()
        .set_index(pd.to_datetime(_minute_frame()["datetime"]))
        .drop(columns="datetime")
    )
    assert len(_normalize_minute_df(indexed)) == 3
    string_index = indexed.copy()
    string_index.index = string_index.index.astype(str)
    assert isinstance(_normalize_minute_df(string_index).index, pd.DatetimeIndex)
    with pytest.raises(ValueError, match="tradingday"):
        _normalize_minute_df(pd.DataFrame({"datetime": ["2020-01-01"], "close": [1]}))

    assert _daily_last_close(pd.DataFrame()).empty
    daily = _daily_last_close(normalized)
    assert daily.loc[pd.Timestamp("2020-01-02")] == 11
    with pytest.raises(ValueError, match="tradingday"):
        _daily_last_close(pd.DataFrame({"close": [1]}))
    with pytest.raises(ValueError, match="close"):
        _daily_last_close(pd.DataFrame({"tradingday": ["2020-01-01"]}))

    assert build_sector_extreme_cache(pd.DataFrame(), {}).copy() == {}
    dates = pd.date_range("2020-01-01", periods=4)
    closes = pd.DataFrame(
        {
            "A2003&A2005": [100, 110, 99, 108],
            "B2003&B2005": [100, 90, 108, 97],
            "IF2003&IF2004": [100, 120, 80, 130],
        },
        index=dates,
    )
    ext = build_sector_extreme_cache(closes, {"A": "农产", "B": "黑色", "IF": "中金所"})
    assert set(ext) == {"农产", "黑色"}
    assert all(len(values) == 4 for values in ext.values())
    assert build_sector_extreme_cache(closes[["IF2003&IF2004"]], {"IF": "中金所"}) == {}


def test_calendar_close_and_peer_resolution() -> None:
    exact = pd.Series([10.0], index=pd.to_datetime(["2020-01-02"]))
    intraday = pd.Series(
        [20.0, 21.0], index=pd.to_datetime(["2020-01-03 09:00", "2020-01-03 15:00"])
    )
    out = build_product_daily_close(
        {
            "A": {"2020-01-02": "A1&A2", "2020-01-03": "A3&A4"},
            "B": {"2020-01-02": "missing"},
        },
        {"A1&A2": exact, "A3&A4": intraday},
    )
    assert out.loc[pd.Timestamp("2020-01-02"), "A"] == 10
    assert out.loc[pd.Timestamp("2020-01-03"), "A"] == 21
    assert build_product_daily_close({"A": {"2020-01-01": "x"}}, {}).empty

    cal = {
        "A": {"2020-01-02": "A1&A2"},
        "B": {"2020-01-02": "B1&B2"},
        "C": {"2020-01-02": "C1&C2"},
    }
    minute = {name: pd.DataFrame({"close": [1]}) for name in ("A1&A2", "B1&B2")}
    assert calendar_board_peers(
        "A1&A2", "2020-01-02", cal, minute, {"A": "x", "B": "x", "C": "y"}, {}, {}
    ) == ["A1&A2", "B1&B2"]
    assert calendar_board_peers("A1&A2", "2020-01-02", cal, minute, {"B": "x"}, {}, {}) == ["A1&A2"]
    assert calendar_board_peers("A1&A2", "2020-01-03", cal, minute, {"A": "x"}, {}, {}) == ["A1&A2"]
    assert calendar_board_peers(
        "A1&A2", "2020-01-02", cal, minute, {}, {"A": "s", "B": "s"}, {}
    ) == ["A1&A2", "B1&B2"]


def test_spread_sector_panel_queries_and_morning_cache(monkeypatch) -> None:
    a = _normalize_minute_df(_minute_frame())
    b = _normalize_minute_df(_minute_frame(10))
    a.loc[pd.Timestamp("2020-01-02 09:01"), "close"] = np.nan
    day_key = tradingday_i64("2020-01-02 15:00")
    panel = SpreadSectorPanel(
        sector_ext_by_sector={"农产": {day_key: 1}},
        spread_to_sector={"A1&A2": "农产"},
        sector_map={"A": "农产", "B": "农产"},
        minute_by_spread={"A1&A2": a, "B1&B2": b},
        board_peers={"A1&A2": ["A1&A2", "B1&B2"]},
        industry_map={"A": "油脂", "B": "油脂"},
        calendar_by_product={
            "A": {"2020-01-02": "A1&A2"},
            "B": {"2020-01-02": "B1&B2"},
        },
    )
    assert panel.sector_ext_for("A1&A2", "2020-01-02") == 1
    assert panel.sector_ext_for("B1&B2", "2020-01-02") == 1
    assert panel.sector_ext_map_for_spread("B1&B2") == {day_key: 1}
    assert panel.board_peers_for("A1&A2", "2020-01-02") == ["A1&A2", "B1&B2"]
    assert panel.day_universe("2020-01-02") == ["A1&A2", "B1&B2"]
    assert panel.day_universe("2020-01-04") == ["A1&A2", "B1&B2"]
    assert len(panel.minute_df("A1&A2")) == 3
    assert panel.minute_df("missing").empty
    assert panel.peer_close_at("A1&A2", "2020-01-02 09:00") == 10
    assert panel.peer_close_at("A1&A2", "2020-01-02 09:01") is None
    assert panel.peer_close_at("missing", "2020-01-02") is None
    assert panel.peer_close_at("A1&A2", "2020-01-02 09:02") is None
    assert panel.peer_first_close_on_day("A1&A2", "2020-01-02", "2020-01-02 09:00") == 10
    assert panel.peer_first_close_on_day("A1&A2", "2020-01-04") is None
    assert panel.peer_first_close_on_day("missing", "2020-01-02") is None
    nan_first = a.copy()
    nan_first.loc[pd.Timestamp("2020-01-02 09:00"), "close"] = np.nan
    panel.minute_by_spread["N1&N2"] = nan_first
    assert panel.peer_first_close_on_day("N1&N2", "2020-01-02") is None
    assert len(panel.bars_on_day_before("A1&A2", "2020-01-02", "2020-01-02 09:01")) == 1
    assert panel.bars_on_day_before("missing", "2020-01-02", "2020-01-02 09:01").empty
    warmup = panel.warmup_minute_df("A1&A2")
    assert warmup.equals(a) and warmup is not a

    package = ModuleType("strategies.wangzhihao")
    module = ModuleType("strategies.wangzhihao.cross_section")
    calls = []

    def build_masks(frames, symbols, mode, calendar_by_product=None):
        calls.append((frames, symbols, mode, calendar_by_product))
        return {"mode": mode}

    module.build_morning_pool_masks = build_masks
    monkeypatch.setitem(sys.modules, "strategies.wangzhihao", package)
    monkeypatch.setitem(sys.modules, "strategies.wangzhihao.cross_section", module)
    assert panel.morning_pool_masks("CV80") == {"mode": "CV80"}
    assert panel.morning_pool_masks("CV80") == {"mode": "CV80"}
    assert len(calls) == 1

    static = SpreadSectorPanel(board_peers={"A1&A2": ["B1&B2"]})
    assert static.board_peers_for("A1&A2") == ["B1&B2"]
    assert static.board_peers_for("C1&C2") == ["C1&C2"]
    assert static.board_peers_for("") == []


class _Source:
    def __init__(self, frames):
        self.frames = frames

    def load_spread(self, key, spread, years):
        value = self.frames[spread]
        if isinstance(value, Exception):
            raise value
        return value


class _Calendar:
    def __init__(self, mapping):
        self.mapping = mapping

    def trading_days(self, product, years):
        return list(self.mapping.get(product, {}))

    def spread_of(self, product, day, tenor):
        return self.mapping[product][day]


class _Schedule:
    tenor = "dom_sub"

    def __init__(self, mapping):
        self.mapping = mapping
        self.calendar = _Calendar(mapping)

    def unique_spreads(self, product, years):
        return list(dict.fromkeys(self.mapping.get(product, {}).values()))


def test_panel_builders_success_filter_and_failure_paths() -> None:
    cfg = SimpleNamespace(products=["A", "B"], exclude=["C"], years=["2020"])
    sector = {"A": "农产", "B": "农产"}
    industry = {"A": "油脂", "B": "油脂"}
    entries = [
        {"enabled": False, "params": {"symbol": "X1&X2"}},
        {"params": {}},
        {"params": {"symbol": " A1&A2 "}},
        {"params": {"symbol": "B1&B2"}},
        {"params": {"symbol": "C1&C2"}},
    ]
    source = _Source({"A1&A2": _minute_frame(), "B1&B2": ValueError("bad")})
    panel = build_panel_from_entries(entries, cfg, None, source, sector, industry)
    assert panel is not None
    assert list(panel.minute_by_spread) == ["A1&A2"]
    assert panel.spread_to_sector == {"A1&A2": "农产"}
    assert build_panel_from_entries([], cfg, None, source, sector) is None

    empty = build_panel_from_entries(
        [{"params": {"symbol": "A1&A2"}}],
        cfg,
        None,
        _Source({"A1&A2": pd.DataFrame()}),
        sector,
    )
    assert empty is not None and not empty.minute_by_spread
    malformed = build_panel_from_entries(
        [{"params": {"symbol": "A1&A2"}}],
        cfg,
        None,
        _Source({"A1&A2": pd.DataFrame({"datetime": ["2020-01-01"], "close": [1]})}),
        sector,
    )
    assert malformed is not None and not malformed.minute_by_spread

    schedule = _Schedule(
        {
            "A": {"2020-01-02": "A1&A2", "2020-01-03": "A3&A4"},
            "B": {"2020-01-02": "B1&B2"},
        }
    )
    scheduled = build_panel_from_schedule(
        schedule,
        ["A", "B"],
        ["2020"],
        cfg,
        None,
        _Source({"A1&A2": _minute_frame(), "A3&A4": pd.DataFrame(), "B1&B2": _minute_frame(5)}),
        sector,
        industry,
    )
    assert scheduled is not None
    assert set(scheduled.minute_by_spread) == {"A1&A2", "B1&B2"}
    assert scheduled.calendar_by_product["A"]["2020-01-03"] == "A3&A4"
    assert (
        build_panel_from_schedule(_Schedule({}), [], ["2020"], cfg, None, _Source({}), sector)
        is None
    )
    scheduled_empty = build_panel_from_schedule(
        _Schedule({"A": {"2020-01-02": "A1&A2"}}),
        ["A"],
        ["2020"],
        cfg,
        None,
        _Source({"A1&A2": RuntimeError("no data")}),
        sector,
    )
    assert scheduled_empty is not None and scheduled_empty.calendar_by_product

    assert not strategy_needs_panel("strategies.foo_spread")
    assert strategy_needs_panel("strategies.xuhe.strategy")
    assert not strategy_needs_panel("strategies.plain")


def test_research_loader_filters_and_forward_labels(tmp_path) -> None:
    path = tmp_path / "spread.csv"
    pd.DataFrame(
        {
            "datetime": ["2020-01-02 09:01", "2020-01-02 09:00"],
            "tradingday": ["2020-01-02", "2020-01-02"],
            "close": ["2", "1"],
            "bidPrice": [1.8, 0.8],
            "askPrice": [2.2, 3.0],
            "trade": [1, 0],
        }
    ).to_csv(path, index=False)
    loaded = load_spread_csv(path)
    assert loaded["datetime"].is_monotonic_increasing
    assert loaded["close"].dtype.kind in "fi"
    assert loaded["trade"].dtype == bool
    assert len(apply_filters(loaded, True, 1)) == 1
    assert len(apply_filters(loaded, False, 1)) == 1

    no_trade_path = tmp_path / "no_trade.csv"
    pd.DataFrame({"datetime": ["2020-01-01"], "close": [1]}).to_csv(no_trade_path, index=False)
    no_trade = load_spread_csv(no_trade_path)
    assert no_trade["trade"].all()
    labels = add_forward_labels(
        pd.DataFrame(
            {"close": [1, 3, 6], "bidPrice": [0.9, 2.9, 5.9], "askPrice": [1.1, 3.1, 6.1]}
        ),
        [1, 2],
    )
    assert labels.loc[0, "fwd_ret_2"] == 5
    assert labels.loc[0, "fwd_realized_long_1"] == pytest.approx(1.8)
    assert "fwd_realized_long_1" not in add_forward_labels(pd.DataFrame({"close": [1, 2]}), [1])


def _ic_frame() -> pd.DataFrame:
    rows = []
    for spread_index, spread in enumerate(("A1&A2", "B1&B2")):
        for day in range(12):
            value = day + spread_index * 0.2
            rows.append(
                {
                    "datetime": pd.Timestamp("2020-01-01")
                    + pd.Timedelta(days=day, hours=spread_index),
                    "tradingday": f"2020-01-{day + 1:02d}",
                    "spread_id": spread,
                    "product": spread[0],
                    "sector": "农产",
                    "factor": value,
                    "fwd_ret_1": value * (1 if spread_index == 0 else -1),
                }
            )
    return pd.DataFrame(rows)


def test_ic_analysis_summary_comparison_quantiles_and_redundancy() -> None:
    assert np.isnan(_rank_corr(pd.Series(range(3)), pd.Series(range(3))))
    assert np.isnan(_rank_corr(pd.Series([1] * 10), pd.Series(range(10))))
    assert _rank_corr(pd.Series(range(10)), pd.Series(range(10))) == pytest.approx(1)
    assert _label_col(2, "x_{h}") == "x_2"
    assert np.isnan(_daily_rank_ic(pd.DataFrame({"a": [1], "b": [2]}), "a", "b"))

    frame = _ic_frame()
    panel = compute_ic_panel(frame, [1, 9], ["factor", "missing"])
    assert set(panel["ic_type"]) == {"timeseries"}
    assert len(panel) == 2
    assert compute_ic_panel(frame.drop(columns="fwd_ret_1"), [1], ["factor"]).empty
    summary = summarize_ic(panel)
    assert list(summary["factor"]) == ["factor"]
    assert "icir" in summary
    assert summarize_ic(pd.DataFrame()).empty
    cross_only = panel.assign(ic_type="cross_section").drop(columns="label_kind")

    positive = summary.assign(ic_mean=0.2)
    realized = summary.assign(label_kind="realized_long", ic_mean=-0.2)
    compared = compare_ic_summaries(positive, realized)
    assert compared["sign_flip"].all()
    assert compare_ic_summaries(pd.DataFrame(), realized).empty

    quantile_data = pd.DataFrame(
        {"factor": np.arange(100), "label": np.arange(100) * 2, "spread_id": ["A"] * 100}
    )
    quantiles = quantile_spread_test(quantile_data, "factor", "label")
    assert len(quantiles) == 5
    assert quantile_spread_test(quantile_data.head(10), "factor", "label").empty
    constant = quantile_data.assign(factor=1)
    assert len(quantile_spread_test(constant, "factor", "label")) <= 1

    corr = pd.DataFrame(
        {
            "z_close": np.arange(200, dtype=float),
            "same": np.arange(200, dtype=float),
            "other": np.sin(np.arange(200)),
            "nan": [np.nan] * 200,
        }
    )
    kept = filter_redundant_factors(corr, ["z_close", "same", "other", "missing"])
    assert kept == ["z_close", "other"]
    assert filter_redundant_factors(corr, ["nan"]) == ["nan"]
    assert filter_redundant_factors(corr.head(10), ["same"]) == ["same"]
    assert filter_redundant_factors(corr.drop(columns="z_close"), ["same"]) == ["same"]
    sampled = filter_redundant_factors(corr, ["other"], sample_rows=100)
    assert sampled == ["other"]
    by_sector = ic_by_sector(panel)
    assert set(by_sector.columns) == {"sector", "factor", "horizon", "rank_ic"}
    assert ic_by_sector(pd.DataFrame()).empty
    assert ic_by_sector(panel.drop(columns="sector")).empty
    assert ic_by_sector(cross_only.assign(sector="农产")).empty


def test_common_utility_branches_and_panel_registry(tmp_path, monkeypatch) -> None:
    assert active_spreads_on_day(
        {"A": {"2020-01-02": "A1&A2"}, "B": {"2020-01-02": "A1&A2"}},
        pd.Timestamp("2020-01-02 21:00"),
    ) == ["A1&A2"]
    assert active_spreads_on_day({"A": {"2020-01-02": "A1&A2"}}, "2020-01-02", []) == []
    assert contract_yyyymm(None, 2020) is None
    assert contract_yyyymm(float("nan"), 2020) is None
    assert contract_yyyymm("A2005", 2020) == 202005
    assert contract_yyyymm("A905", 2019) == 201905
    assert contract_yyyymm("A9912", 2020) == 199912
    assert contract_yyyymm("A12", 2020) is None
    assert contract_yyyymm("A2013", 2020) is None
    assert product_of(" a2005 ") == "A"
    assert product_of("bad-contract") == ""
    assert spread_from_legs(" A1 ", " A2 ") == "A1&A2"
    assert normalize_tenor("anything", for_mode="calendar_dom_sub") == "dom_sub"
    assert normalize_tenor(None) == "dom_sub"
    with pytest.raises(ValueError, match="tenor"):
        normalize_tenor("bad")

    assert leg0_product("a1&a2") == "A"
    assert board_of_product("a", {"A": "x"}) == "x"
    assert board_of_product("b", {"A": "x"}) is None
    peers = build_board_peers(["A1&A2", "B1&B2", "C1&C2"], {"A": "x", "B": "x"})
    assert peers["A1&A2"] == ["A1&A2", "B1&B2"]
    assert peers["C1&C2"] == []
    assert sector_ext_at(None, "A1&A2", "2020-01-01") == 0
    assert sector_ext_at(object(), "A1&A2", "2020-01-01") == 0
    assert sector_ext_at(SimpleNamespace(sector_ext_for=lambda *_: "1"), "A", "2020-01-01") == 1
    assert sector_ext_at(SimpleNamespace(sector_ext_for=lambda *_: 1 / 0), "A", "2020-01-01") == 0

    assert round_down(1.29, 0.1) == pytest.approx(1.2)
    assert round_up(1.21, 0.1) == pytest.approx(1.3)
    assert Boll([], 2, 1, 1) == {"long": [], "short": []}
    assert Boll([1, 2], 2, 0, 1) == {"long": [], "short": []}
    assert Boll([1, 2], 2, 1, 0) == {"long": [], "short": []}
    boll = Boll([1, 2, 3], 2, 2, 0.1)
    assert len(boll["long"]) == len(boll["short"]) == 2
    assert Boll3([], 2, 1) == {"long": [], "short": []}
    assert Boll3([1, 2], 2, 0) == {"long": [], "short": []}
    assert len(Boll3([1, 2, 3], 2, 0.1, nb_offset=0.2)["long"]) == 2

    monkeypatch.setattr(panel_registry, "_CACHE_DIR", tmp_path)
    panel_registry.clear_panels()
    assert panel_registry.get_panel("") is None
    legacy = SimpleNamespace(value=1)
    panel_registry.register_panel("run", legacy)
    assert panel_registry.get_panel("run").calendar_by_product == {}
    panel_registry.clear_panels()
    loaded = panel_registry.get_panel("run")
    assert loaded.value == 1
    assert panel_registry.get_panel("missing") is None
    panel_registry.register_panel("", object())
    assert not (tmp_path / ".pkl").exists()
    with (tmp_path / "manual.pkl").open("wb") as file_handle:
        pickle.dump(SimpleNamespace(calendar_by_product={"A": {}}), file_handle)
    panel_registry.clear_panels()
    assert panel_registry.get_panel("manual").calendar_by_product == {"A": {}}
