"""tests/test_universe_calendar.py — 主力表 calendar / roll governor。"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from core.data.dominant_calendar import DominantCalendar
from core.data.spread_schedule import SpreadSchedule, cal_map_for
from core.data.universe import expand_calendar_universe
from core.engine.roll_governor import RollGovernor, RollState
from core.io.config_loader import load_backtest_config
from framework.base import CLOSE_LONG, OPEN_LONG, TargetOrder
from utils.contract_util import FutureList


def ok(cond, msg):
    assert cond, msg


def _write_dom_table(root: Path) -> Path:
    rows = [
        ("2020-04-07", "A", "主力", "A2005"),
        ("2020-04-07", "A", "次主力", "A2009"),
        ("2020-04-07", "A", "次次主力", "A2101"),
        ("2020-04-08", "A", "主力", "A2009"),
        ("2020-04-08", "A", "次主力", "A2101"),
        ("2020-04-08", "A", "次次主力", "A2105"),
    ]
    df = pd.DataFrame(rows, columns=["tradingday", "product", "contract_type", "contract"])
    p = root / "主力合约表-2020.csv"
    df.to_csv(p, index=False)
    return root


def test_spread_schedule_dom_sub():
    with tempfile.TemporaryDirectory() as tmp:
        d = _write_dom_table(Path(tmp))
        sched = SpreadSchedule.from_dir(str(d), "dom_sub")
        ok(sched.spread_on("A", "2020-04-07") == "A2005&A2009", "04-07 dom_sub")
        ok(sched.spread_on("A", "2020-04-08") == "A2009&A2101", "04-08 dom_sub")


def test_spread_schedule_sub_subsub():
    with tempfile.TemporaryDirectory() as tmp:
        d = _write_dom_table(Path(tmp))
        sched = SpreadSchedule.from_dir(str(d), "sub_subsub")
        ok(sched.spread_on("A", "2020-04-07") == "A2009&A2101", "04-07 sub_subsub")
        ok(sched.spread_on("A", "2020-04-08") == "A2101&A2105", "04-08 sub_subsub")


def test_roll_governor_defer_until_flat():
    gov = RollGovernor(trade_symbol="A2005&A2009")
    eff = gov.effective_symbol("A2009&A2101", net_qty=1.0, tradingday="2020-04-08")
    ok(eff == "A2005&A2009", "有仓不切")
    ok(gov.state == RollState.CLOSING_OUT, "进入 closing_out")

    opens = [TargetOrder("A2005&A2009", OPEN_LONG, 100.0, 1.0)]
    closes = [TargetOrder("A2005&A2009", CLOSE_LONG, 100.0, 1.0)]
    ok(len(gov.filter_targets(opens)) == 0, "禁 OPEN")
    ok(len(gov.filter_targets(closes)) == 1, "允许 CLOSE")

    gov.after_bar("A2009&A2101", net_qty=0.0, tradingday="2020-04-08")
    ok(gov.trade_symbol == "A2009&A2101", "flat 后切换")
    ok(gov.state == RollState.NORMAL, "回到 normal")
    reasons = [e.reason for e in gov.events]
    ok("enter_closing_out" in reasons and "closed_out" in reasons, "记录换月事件")


def test_roll_governor_flat_switch():
    gov = RollGovernor(trade_symbol="A2005&A2009")
    eff = gov.effective_symbol("A2009&A2101", net_qty=0.0, tradingday="2020-04-08")
    ok(eff == "A2009&A2101", "空仓立即切换")
    ok(gov.events[0].reason == "flat_switch", "flat_switch 事件")


def test_expand_calendar_universe():
    with tempfile.TemporaryDirectory() as tmp:
        d = _write_dom_table(Path(tmp))
        sched = SpreadSchedule.from_dir(str(d), "dom_sub")
        fl = FutureList.load(str(Path(__file__).resolve().parent.parent / "config" / "future_list.csv"))
        inst = expand_calendar_universe(
            [{"id": "gz2", "module": "strategies.gaozong2_spread.strategy", "enabled": True, "params": {}}],
            sched, ["A"], ["2020"], [], fl, False,
        )
        ok(len(inst) == 1, "1 product 1 instance")
        ok(inst[0]["id"] == "gz2__A", "id 格式")
        ok(inst[0]["params"]["product"] == "A", "product param")


def test_cal_map_per_strategy_tenor():
    with tempfile.TemporaryDirectory() as tmp:
        d = _write_dom_table(Path(tmp))
        cal = DominantCalendar(str(d))
        dom = cal_map_for(cal, "A", ["2020"], "dom_sub")
        sub = cal_map_for(cal, "A", ["2020"], "sub_subsub")
        ok(dom["2020-04-07"] == "A2005&A2009", "dom_sub")
        ok(sub["2020-04-07"] == "A2009&A2101", "sub_subsub 临期")


def test_run_bar_loop_triggers_symbol_switch():
    from core.engine.runner import _run_bar_loop
    from core.engine.context import BacktestStrategyContext
    from core.engine.reconcile_sim import ReconcileSimulator
    from core.engine.roll_governor import RollGovernor
    from core.data.market_store import InMemoryMarketStore
    from core.portfolio.position_book import BacktestPositionBook
    from core.portfolio.accounting import SpreadAccounting
    from core.engine.signal_recorder import SignalRecorder
    from core.portfolio.commission import SpreadOpenDayTracker
    from core.types import BarData
    from framework.base import Strategy as BaseStrategy

    class _Probe(BaseStrategy):
        def __init__(self, sid, params, ctx):
            super().__init__(sid, params, ctx)
            self.switches: list[tuple[str, str]] = []

        def on_symbol_switch(self, old_symbol: str, new_symbol: str) -> None:
            super().on_symbol_switch(old_symbol, new_symbol)
            self.switches.append((old_symbol, new_symbol))

    def _bar(sym: str, td: str, dt: str, close: float) -> BarData:
        return BarData(
            symbol=sym, exchange="DCE", interval="1m", source="comb",
            datetime=datetime.fromisoformat(dt), product_id="A",
            open_price=close, high_price=close, low_price=close, close_price=close,
            volume=1.0, trading_day=td, tradable=True,
        )

    old, new = "A2005&A2009", "A2009&A2101"
    bars = [
        _bar(old, "2020-04-07", "2020-04-07 09:00:00", 100.0),
        _bar(new, "2020-04-08", "2020-04-08 09:00:00", 110.0),
    ]
    cal = {"2020-04-07": old, "2020-04-08": new}
    market = InMemoryMarketStore()
    book = BacktestPositionBook()
    ctx = BacktestStrategyContext("p", market, book, {}, {})
    strat = _Probe("p", {"symbol": old}, ctx)
    sim = ReconcileSimulator()
    acct = SpreadAccounting(10.0, 1_000_000.0)
    gov = RollGovernor(trade_symbol=old)
    fl = FutureList.load(str(Path(__file__).resolve().parent.parent / "config" / "future_list.csv"))
    comm_tracker = SpreadOpenDayTracker()
    _run_bar_loop(
        "p", strat, sim, book, acct, market, bars, old,
        fl, comm_tracker, gov, cal,
        SignalRecorder("probe"), ctx, "DCE", 1.0,
    )
    ok(strat.switches == [(old, new)], "bar 循环内触发换 spread")
    ok(strat.symbol == new, "最终 symbol 为新 spread")


def test_apply_symbol_switch_calls_hook():
    from core.engine.runner import _apply_symbol_switch
    from core.engine.context import BacktestStrategyContext
    from core.engine.reconcile_sim import ReconcileSimulator
    from core.data.market_store import InMemoryMarketStore
    from core.portfolio.position_book import BacktestPositionBook
    from framework.base import Strategy as BaseStrategy

    class _Probe(BaseStrategy):
        def __init__(self, sid, params, ctx):
            super().__init__(sid, params, ctx)
            self.switches: list[tuple[str, str]] = []

        def on_symbol_switch(self, old_symbol: str, new_symbol: str) -> None:
            super().on_symbol_switch(old_symbol, new_symbol)
            self.switches.append((old_symbol, new_symbol))

    market = InMemoryMarketStore()
    book = BacktestPositionBook()
    ctx = BacktestStrategyContext("p", market, book, {}, {})
    strat = _Probe("p", {"symbol": "A2005&A2009"}, ctx)
    sim = ReconcileSimulator()
    _apply_symbol_switch(
        strat, sim, "p", "A2005&A2009", "A2009&A2101", ctx, "DCE", 1.0,
        market, {}, "A", "comb", None,
    )
    ok(strat.symbol == "A2009&A2101", "symbol 更新")
    ok(strat.switches == [("A2005&A2009", "A2009&A2101")], "hook 被调用")
    ok(sim._pending.get(("p", "a2005&a2009")) is None, "旧 symbol 挂单已清")


def test_gaozong2_symbol_switch_resets_state():
    pytest = __import__("pytest")
    try:
        from strategies.gaozong2.strategy import Strategy
    except ModuleNotFoundError:
        pytest.skip(
            "LEGACY_SKIP owner=quant-futures-spread maintainers; "
            "unblock when the legacy gaozong2 strategy is restored or this legacy test is retired"
        )
    from core.engine.runner import _apply_symbol_switch
    from core.engine.context import BacktestStrategyContext
    from core.engine.reconcile_sim import ReconcileSimulator
    from core.data.market_store import InMemoryMarketStore
    from core.portfolio.position_book import BacktestPositionBook

    market = InMemoryMarketStore()
    book = BacktestPositionBook()
    ctx = BacktestStrategyContext("gz", market, book, {}, {})
    strat = Strategy("gz", {"symbol": "A2005&A2009", "run_id": "x"}, ctx)
    strat.on_init()
    old_state_id = id(strat._state)
    _apply_symbol_switch(
        strat, ReconcileSimulator(), "gz", "A2005&A2009", "A2009&A2101", ctx,
        "DCE", 1.0, market, {}, "A", "comb", None,
    )
    ok(strat._state.symbol == "A2009&A2101", "streaming state symbol 更新")
    ok(id(strat._state) != old_state_id, "streaming state 重建")


def test_config_rejects_bad_roll_on_switch():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.yaml"
        p.write_text(
            "run_id: x\nuniverse:\n  mode: calendar_dom_sub\n"
            "roll:\n  on_switch: immediate\n"
            "data:\n  years: ['2020']\noutput:\n  dir: output\n",
            encoding="utf-8",
        )
        cdir = Path(__file__).resolve().parent.parent / "config"
        try:
            load_backtest_config(str(p), str(cdir))
            ok(False, "应拒绝非 defer_until_flat")
        except ValueError as e:
            ok("defer_until_flat" in str(e), "错误信息含 defer_until_flat")


def test_config_rejects_auto_universe():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.yaml"
        p.write_text(
            "run_id: x\nuniverse:\n  auto: true\n"
            "data:\n  years: ['2020']\noutput:\n  dir: output\n",
            encoding="utf-8",
        )
        cdir = Path(__file__).resolve().parent.parent / "config"
        try:
            load_backtest_config(str(p), str(cdir))
            ok(False, "应拒绝 auto")
        except ValueError as e:
            ok("universe.auto" in str(e), "错误信息含 universe.auto")


def test_calendar_config_loads():
    cfg = load_backtest_config(
        "config/backtest_example_dom_sub.yaml",
    )
    ok(cfg.universe_mode == "calendar_dom_sub", "mode")
    ok(cfg.tenor == "dom_sub", "tenor for calendar_dom_sub")


def test_market_store_clear_symbol():
    from datetime import datetime
    from core.data.market_store import InMemoryMarketStore
    from core.types import BarData

    store = InMemoryMarketStore()
    old, new = "A2005&A2009", "A2009&A2101"
    for sym in (old, new):
        store.append(BarData(
            symbol=sym, exchange="DCE", interval="1m", source="comb",
            datetime=datetime(2020, 4, 7, 9, 0), product_id="A",
            open_price=1.0, high_price=1.0, low_price=1.0, close_price=1.0,
            volume=1.0, trading_day="2020-04-07", tradable=True,
        ))
    ok(len(store.query_bars(old, "DCE", source="comb")) == 1, "旧 symbol 有 bar")
    store.clear_symbol(old, "DCE", "1m", "comb")
    ok(len(store.query_bars(old, "DCE", source="comb")) == 0, "clear 后旧 symbol 无 bar")
    ok(len(store.query_bars(new, "DCE", source="comb")) == 1, "新 symbol 不受影响")


def test_panel_calendar_board_peers():
    import pandas as pd
    from core.panel.sector_panel import SpreadSectorPanel

    cal = {
        "A": {"2020-04-07": "A2005&A2009", "2020-04-08": "A2009&A2101"},
        "B": {"2020-04-07": "B2005&B2009", "2020-04-08": "B2009&B2101"},
    }
    minute = {
        "A2005&A2009": pd.DataFrame({"close": [1.0], "tradingday": ["2020-04-07"]}, index=pd.DatetimeIndex(["2020-04-07"])),
        "B2005&B2009": pd.DataFrame({"close": [2.0], "tradingday": ["2020-04-07"]}, index=pd.DatetimeIndex(["2020-04-07"])),
        "A2009&A2101": pd.DataFrame({"close": [3.0], "tradingday": ["2020-04-08"]}, index=pd.DatetimeIndex(["2020-04-08"])),
        "B2009&B2101": pd.DataFrame({"close": [4.0], "tradingday": ["2020-04-08"]}, index=pd.DatetimeIndex(["2020-04-08"])),
    }
    panel = SpreadSectorPanel(
        minute_by_spread=minute,
        sector_map={"A": "NCP", "B": "NCP"},
        spread_to_sector={"A2005&A2009": "NCP", "B2005&B2009": "NCP", "A2009&A2101": "NCP", "B2009&B2101": "NCP"},
        calendar_by_product=cal,
    )
    p7 = panel.board_peers_for("A2005&A2009", "2020-04-07")
    ok("B2005&B2009" in p7 and "A2009&A2101" not in p7, "4/7 仅当日 calendar peer")
    p8 = panel.board_peers_for("A2009&A2101", "2020-04-08")
    ok("B2009&B2101" in p8, "4/8 新 spread peer")


def test_apply_symbol_switch_fallback_on_exception():
    from core.engine.runner import _apply_symbol_switch
    from core.engine.context import BacktestStrategyContext
    from core.engine.reconcile_sim import ReconcileSimulator
    from core.data.market_store import InMemoryMarketStore
    from core.portfolio.position_book import BacktestPositionBook
    from framework.base import Strategy as BaseStrategy

    class _Broken(BaseStrategy):
        def on_symbol_switch(self, old_symbol: str, new_symbol: str) -> None:
            raise RuntimeError("boom")

    market = InMemoryMarketStore()
    book = BacktestPositionBook()
    ctx = BacktestStrategyContext("p", market, book, {}, {})
    strat = _Broken("p", {"symbol": "A2005&A2009"}, ctx)
    _apply_symbol_switch(
        strat, ReconcileSimulator(), "p", "A2005&A2009", "A2009&A2101", ctx,
        "DCE", 1.0, market, {}, "A", "comb", None,
    )
    ok(strat.symbol == "A2009&A2101", "hook 异常仍强制更新 symbol")


if __name__ == "__main__":
    test_spread_schedule_dom_sub()
    test_spread_schedule_sub_subsub()
    test_roll_governor_defer_until_flat()
    test_roll_governor_flat_switch()
    test_expand_calendar_universe()
    test_cal_map_per_strategy_tenor()
    test_run_bar_loop_triggers_symbol_switch()
    test_apply_symbol_switch_calls_hook()
    test_gaozong2_symbol_switch_resets_state()
    test_config_rejects_bad_roll_on_switch()
    test_config_rejects_auto_universe()
    test_calendar_config_loads()
    test_market_store_clear_symbol()
    test_panel_calendar_board_peers()
    test_apply_symbol_switch_fallback_on_exception()
    print("ALL UNIVERSE CALENDAR TESTS PASSED")
