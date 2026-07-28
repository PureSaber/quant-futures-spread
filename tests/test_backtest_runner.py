"""tests/test_backtest_runner.py —— backtest 引擎骨架测试（mock 小样本 CSV）。"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from framework.base import TargetOrder, OPEN_LONG, CLOSE_LONG
from core.data.bar_converter import df_to_bars
from core.io.config_loader import load_backtest_config
from core.engine.reconcile_sim import ReconcileSimulator
from core.engine.runner import run_instance
from core.types import BarData
from utils.contract_util import FutureList

_ROOT = Path(__file__).resolve().parent.parent


def ok(cond, msg):
    assert cond, msg


def test_bar_converter():
    df = pd.DataFrame({
        "datetime": pd.date_range("2020-01-02 09:01", periods=5, freq="min"),
        "tradingday": ["2020-01-02"] * 5,
        "open": [10, 11, 12, 11, 10],
        "high": [11, 12, 13, 12, 11],
        "low": [9, 10, 11, 10, 9],
        "close": [10.5, 11.5, 12.5, 11.5, 10.5],
        "trade": [True] * 5,
    })
    bars = df_to_bars(df, "A2003&A2005", "DCE", product_id="A")
    ok(len(bars) == 5, f"转换 5 根 bar (got {len(bars)})")
    ok(bars[-1].close_price == 10.5, "close 对齐")


def test_reconcile_sim_open_long():
    from core.portfolio.position_book import BacktestPositionBook
    sim = ReconcileSimulator()
    book = BacktestPositionBook()
    sym = "A2003&A2005"
    sim.reconcile("s1", sym, [TargetOrder(sym, OPEN_LONG, 100.0, 1, "L0")], trade_mode=1)
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        open_price=101, high_price=102, low_price=99, close_price=101,
        source="comb", trading_day="2020-01-02",
    )
    trades = sim.try_fill(bar, book)
    ok(len(trades) == 1, "低价触达开多限价应成交")
    ok(trades[0].direction == "LONG" and trades[0].offset == "OPEN", "开多方向")

    sim.reconcile("s1", sym, [TargetOrder(sym, OPEN_LONG, 95.0, 1)], trade_mode=1)
    bar2 = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 1),
        open_price=101, high_price=102, low_price=100, close_price=101,
        source="comb", trading_day="2020-01-02",
    )
    ok(len(sim.try_fill(bar2, book)) == 0, "限价未触达不成交")


def test_reconcile_bid_ask_open_long():
    from core.portfolio.position_book import BacktestPositionBook
    sim = ReconcileSimulator()
    book = BacktestPositionBook()
    sym = "A2003&A2005"
    sim.reconcile("s1", sym, [TargetOrder(sym, OPEN_LONG, 100.0, 1, "L0")], trade_mode=1)
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        open_price=101, high_price=102, low_price=99, close_price=101,
        ask_low=99, ask_high=101, bid_low=98, bid_high=100,
        source="comb", trading_day="2020-01-02",
    )
    trades = sim.try_fill(bar, book)
    ok(len(trades) == 1, "ask_low 触达开多限价应成交")
    bar2 = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 1),
        low_price=100, high_price=101, close_price=101,
        ask_low=100.5, ask_high=101.5, bid_low=99, bid_high=100,
        source="comb", trading_day="2020-01-02",
    )
    ok(len(sim.try_fill(bar2, book)) == 0, "ask 未触达不成交")


def test_reconcile_open_dedup():
    from core.portfolio.position_book import BacktestPositionBook
    sim = ReconcileSimulator()
    book = BacktestPositionBook()
    sym = "A2003&A2005"
    sim.reconcile("s1", sym, [TargetOrder(sym, OPEN_LONG, 100.0, 1)], trade_mode=1)
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        low_price=99, high_price=101, close_price=100, source="comb", trading_day="2020-01-02",
    )
    t1 = sim.try_fill(bar, book)
    book.apply_trade("s1", sym, t1[0].direction, t1[0].offset, t1[0].volume, t1[0].price)
    t2 = sim.try_fill(bar, book)
    ok(len(t1) == 1 and len(t2) == 0, "同一开仓价未平仓前不重复成交")


def test_reconcile_trade_mode():
    from core.portfolio.position_book import BacktestPositionBook
    from framework.base import OPEN_SHORT
    sim = ReconcileSimulator()
    book = BacktestPositionBook()
    sym = "A2003&A2005"
    sim.reconcile("s1", sym, [
        TargetOrder(sym, OPEN_LONG, 100.0, 1),
        TargetOrder(sym, OPEN_SHORT, 110.0, 1),
    ], trade_mode=1)
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        low_price=99, high_price=111, close_price=105,
        source="comb", trading_day="2020-01-02",
    )
    trades = sim.try_fill(bar, book)
    ok(len(trades) == 1 and trades[0].direction == "LONG", "trade_mode=1 只开多")


def test_ref_leg_close_median():
    from core.portfolio.sizing import ref_leg_close_from_df
    df = pd.DataFrame({"close_x": [3000.0, 3100.0, 3200.0, 3300.0]})
    ok(ref_leg_close_from_df(df) == 3150.0, "close_x 中位折手数")


def test_spread_accounting_bar_start_mtm():
    from core.portfolio.accounting import SpreadAccounting
    from core.types import TradeData
    from datetime import datetime

    acct = SpreadAccounting(point=10.0, capital=1_000_000)
    # bar1: flat, close 100
    acct.mark_bar("2020-01-02", 100.0, 0.0)
    # bar2: open long 1@98 at end, close 101 — 应用 bar 初持仓 0 盯市 + inbar
    tr = TradeData("s1", "A2003&A2005", "DCE", "o1", "t1", "LONG", "OPEN", 98.0, 1.0,
                   datetime(2020, 1, 2, 10, 1), "2020-01-02")
    inbar = SpreadAccounting.inbar_pnl([tr], 101.0, 10.0)
    ok(inbar == 30.0, f"inbar open (got {inbar})")
    acct.mark_bar("2020-01-02", 101.0, 0.0, commission=8.0, inbar=inbar)
    # bar3: 持有多头 1@101，close 102 且平多 1@101.5
    tr_close = TradeData("s1", "A2003&A2005", "DCE", "o2", "t2", "SHORT", "CLOSE", 101.5, 1.0,
                         datetime(2020, 1, 2, 10, 2), "2020-01-02")
    inbar2 = SpreadAccounting.inbar_pnl([tr_close], 102.0, 10.0)
    ok(inbar2 == -5.0, f"inbar close (got {inbar2})")
    acct.mark_bar("2020-01-02", 102.0, 1.0, commission=8.0, inbar=inbar2)
    ok(abs(acct._bar_rows[-1]["cum_pnl"] - 19.0) < 1e-9, "开平三 bar 累计 PnL=19")
    daily = acct.daily_returns()
    ok(len(daily) == 1, "同一交易日合并")
    ok(float(daily.iloc[0]) > 0, "开平路径应有正收益贡献")


def test_reconcile_open_reset_on_flat_transition():
    from core.portfolio.position_book import BacktestPositionBook
    sim = ReconcileSimulator()
    book = BacktestPositionBook()
    sym = "A2003&A2005"
    sim.reconcile("s1", sym, [TargetOrder(sym, OPEN_LONG, 100.0, 1)], trade_mode=1)
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        low_price=99, high_price=101, close_price=100, source="comb", trading_day="2020-01-02",
    )
    t1 = sim.try_fill(bar, book)
    for tr in t1:
        book.apply_trade("s1", sym, tr.direction, tr.offset, tr.volume, tr.price)
    sim.reset_opens_if_flat("s1", sym, book)
    sim.reconcile("s1", sym, [TargetOrder(sym, OPEN_LONG, 100.0, 1)], trade_mode=1)
    ok(len(sim.try_fill(bar, book)) == 0, "持仓中不清 open_filled，同价不再开")
    book.apply_trade("s1", sym, "SHORT", "CLOSE", 1.0, 101.0)
    sim.reset_opens_if_flat("s1", sym, book)
    sim.reconcile("s1", sym, [TargetOrder(sym, OPEN_LONG, 100.0, 1)], trade_mode=1)
    ok(len(sim.try_fill(bar, book)) == 1, "平仓后允许同价再开")


def test_reconcile_close_no_refill():
    from core.portfolio.position_book import BacktestPositionBook
    from framework.base import CLOSE_LONG
    sim = ReconcileSimulator()
    book = BacktestPositionBook()
    sym = "A2003&A2005"
    book.apply_trade("s1", sym, "LONG", "OPEN", 1.0, 100.0)
    sim.reconcile("s1", sym, [TargetOrder(sym, CLOSE_LONG, 102.0, 1, "TP")], trade_mode=1)
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        low_price=99, high_price=103, close_price=102,
        source="comb", trading_day="2020-01-02",
    )
    t1 = sim.try_fill(bar, book)
    for tr in t1:
        book.apply_trade("s1", sym, tr.direction, tr.offset, tr.volume, tr.price)
    t2 = sim.try_fill(bar, book)
    ok(len(t1) == 1 and t2 == [], "平仓成交后不再重复撮合")
    ok(book.get_strategy_position("s1", sym).long_qty <= 1e-9, "持仓已平")


def test_reconcile_close_requires_position():
    from core.portfolio.position_book import BacktestPositionBook
    from framework.base import CLOSE_LONG
    sim = ReconcileSimulator()
    book = BacktestPositionBook()
    sym = "A2003&A2005"
    sim.reconcile("s1", sym, [TargetOrder(sym, CLOSE_LONG, 102.0, 1)], trade_mode=1)
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        low_price=99, high_price=103, close_price=102,
        source="comb", trading_day="2020-01-02",
    )
    ok(sim.try_fill(bar, book) == [], "无持仓时不应平多成交")


def test_load_backtest_config():
    cfg = load_backtest_config(str(_ROOT / "config/backtest_example_cross_product.yaml"))
    ok(cfg.run_id == "example_cross_product_2020", "run_id")
    ok("A" in cfg.products and "B" in cfg.products, "universe products")
    ok(len(cfg.strategies) == 3, f"3 跨品种实例 (got {len(cfg.strategies)})")


def _write_mini_csv(data_dir: Path) -> None:
    p = data_dir / "2020" / "A" / "A"
    p.mkdir(parents=True)
    dts = pd.date_range("2020-01-02 09:00", periods=30, freq="min")
    df = pd.DataFrame({
        "datetime": dts.strftime("%Y-%m-%d %H:%M:%S"),
        "tradingday": "2020-01-02",
        "open": [100 + (i % 5) for i in range(30)],
        "high": [102 + (i % 5) for i in range(30)],
        "low": [98 + (i % 5) for i in range(30)],
        "close": [100 + (i % 5) for i in range(30)],
        "close_x": 3000.0,
        "close_y": 2900.0,
        "trade": True,
    })
    df.to_csv(p / "A2003&A2005.csv", index=False)


def test_run_instance_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        _write_mini_csv(data_dir)
        fl = FutureList.load(str(_ROOT / "config/future_list.csv"))
        from data_sources import build_source

        source = build_source("csv_spread", {"data_dir": str(data_dir)})
        entry = {
            "id": "smoke__a2003_a2005",
            "module": "strategies.example_cross_product.strategy",
            "enabled": True,
            "params": {
                "symbol": "A2003&A2005",
                "source": "comb",
                "lookback": 10,
                "entry_z": 0.5,
                "vol_per_layer": [1],
            },
        }
        from core.io.config_loader import BacktestConfig

        cfg = BacktestConfig(
            run_id="smoke",
            strategies=[entry],
            data_dir=str(data_dir),
            years=["2020"],
            future_list_path=str(_ROOT / "config/future_list.csv"),
            products=["A"],
            exclude=[],
            use_trade_flag=False,
            output_dir=str(data_dir / "out"),
            capital=1_000_000,
        )
        res = run_instance(entry, cfg, fl, source)
        ok(len(res.daily_ret) >= 1, f"产出日收益 (days={len(res.daily_ret)})")
        ok(set(["daily_pnl", "daily_pnl_pct", "commission", "num_trades", "win_trades"])
           <= set(res.daily.columns), "daily_frame 字段对齐 CTA")


def _write_spread_csv(data_dir: Path, spread: str) -> None:
    p = data_dir / "2020" / "A" / "A"
    p.mkdir(parents=True, exist_ok=True)
    dts = pd.date_range("2020-01-02 09:00", periods=30, freq="min")
    df = pd.DataFrame({
        "datetime": dts.strftime("%Y-%m-%d %H:%M:%S"),
        "tradingday": "2020-01-02",
        "open": [100 + (i % 5) for i in range(30)],
        "high": [102 + (i % 5) for i in range(30)],
        "low": [98 + (i % 5) for i in range(30)],
        "close": [100 + (i % 5) for i in range(30)],
        "close_x": 3000.0,
        "close_y": 2900.0,
        "trade": True,
    })
    df.to_csv(p / f"{spread}.csv", index=False)


def _make_two_instance_cfg(data_dir: Path, jobs: int):
    from core.io.config_loader import BacktestConfig

    def _entry(symbol):
        return {
            "id": f"smoke__{symbol.replace('&', '_').lower()}",
            "module": "strategies.example_cross_product.strategy",
            "enabled": True,
            "params": {
                "symbol": symbol, "source": "comb",
                "lookback": 10, "entry_z": 0.5,
                "vol_per_layer": [1],
            },
        }

    return BacktestConfig(
        run_id="smoke_parallel",
        strategies=[_entry("A2003&A2005"), _entry("A2005&A2007")],
        data_dir=str(data_dir),
        years=["2020"],
        future_list_path=str(_ROOT / "config/future_list.csv"),
        products=["A"],
        exclude=[],
        use_trade_flag=False,
        output_dir=str(data_dir / "out"),
        capital=1_000_000,
        jobs=jobs,
    )


def test_parallel_equals_sequential():
    """jobs=2 多进程结果应与 jobs=1 串行逐笔一致。"""
    from core.engine.runner import run_backtest
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        _write_spread_csv(data_dir, "A2003&A2005")
        _write_spread_csv(data_dir, "A2005&A2007")
        seq = run_backtest(_make_two_instance_cfg(data_dir, jobs=1))
        par = run_backtest(_make_two_instance_cfg(data_dir, jobs=2))
        ok(set(seq.instances) == set(par.instances), "并行/串行实例集合一致")
        for iid, ir in seq.instances.items():
            pd.testing.assert_series_equal(ir.daily_ret, par.instances[iid].daily_ret)
            pd.testing.assert_frame_equal(ir.daily, par.instances[iid].daily)
        ok(seq.portfolio_daily is not None and par.portfolio_daily is not None,
           "组合非空")
        pd.testing.assert_series_equal(seq.portfolio_daily, par.portfolio_daily)


def test_signal_recorder_dedup_and_schema():
    from core.engine.signal_recorder import SignalRecorder, _COLUMNS
    sym = "A2003&A2005"
    bar = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 0),
        open_price=10, high_price=11, low_price=9, close_price=10.5,
        volume=100, open_interest=200, source="comb", trading_day="2020-01-02",
    )
    rec = SignalRecorder("example_cross_product")
    t0 = TargetOrder(sym, OPEN_LONG, 100.0, 2, "L0-open")
    rec.record_targets(sym, [t0], bar)
    rec.record_targets(sym, [t0], bar)
    bar2 = BarData(
        symbol=sym, exchange="DCE", datetime=datetime(2020, 1, 2, 10, 1),
        open_price=10, high_price=11, low_price=9, close_price=10.6,
        volume=110, open_interest=210, source="comb", trading_day="2020-01-02",
    )
    rec.record_targets(sym, [TargetOrder(sym, OPEN_LONG, 99.0, 2, "L0-open")], bar2)
    rec.record_targets(sym, [TargetOrder(sym, CLOSE_LONG, 101.0, 2, "boll-tp-close")], bar2)
    df = rec.to_dataframe()
    ok(len(df) == 3, f"同价量去重后 3 条信号 (got {len(df)})")
    ok(list(df.columns) == list(_COLUMNS), "signals 列与 CTA schema 一致")
    ok(df.iloc[0]["signal_kind"] == "entry" and df.iloc[0]["direction"] == 1, "开多 entry/+1")
    ok(df.iloc[-1]["signal_kind"] == "take_profit", "tag 含 tp → take_profit")


def test_write_outputs_files():
    """产出落盘：portfolio 策略净值 + symbol 各套利对净值。"""
    from core.engine.runner import run_backtest
    from core.io.output import write_outputs, portfolio_nav_frame, spread_nav_frame
    from core.io.output_format import PORTFOLIO_COL_CN, SPREAD_COL_CN, SUMMARY_COL_CN
    from core.io.output_format import format_portfolio_nav, format_spread_nav
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        _write_spread_csv(data_dir, "A2003&A2005")
        _write_spread_csv(data_dir, "A2005&A2007")
        cfg = _make_two_instance_cfg(data_dir, jobs=1)
        result = run_backtest(cfg)
        out_root = Path(write_outputs(cfg, result))
        port_path = out_root / "daily/portfolio" / f"daily_pnl_portfolio_{cfg.run_id}.csv"
        sym_path = out_root / "daily/symbol" / f"daily_pnl_{cfg.run_id}.csv"
        ok(port_path.exists(), "产出 daily/portfolio")
        ok(sym_path.exists(), "产出 daily/symbol")
        ok(not (out_root / "daily/daily_pnl.csv").exists(), "不产出旧版 daily/daily_pnl.csv")
        port = pd.read_csv(port_path)
        sym = pd.read_csv(sym_path)
        ok("品种" not in port.columns, "portfolio 无品种列")
        ok(SPREAD_COL_CN["spread"] in sym.columns, "symbol 含套利对列")
        ok(list(port.columns) == [PORTFOLIO_COL_CN[c] for c in PORTFOLIO_COL_CN],
           f"portfolio 列: {list(port.columns)}")
        pd.testing.assert_frame_equal(port, format_portfolio_nav(portfolio_nav_frame(result, cfg)))
        pd.testing.assert_frame_equal(sym, format_spread_nav(spread_nav_frame(result, cfg)))
        summary = pd.read_csv(out_root / "performance/summary.csv", index_col=0)
        ok(len(summary) == 1 and cfg.run_id in summary.index, "summary 仅一条 run 汇总")
        ok({SUMMARY_COL_CN["num_trades"], SUMMARY_COL_CN["total_commission"]}
           <= set(summary.columns), "summary 含成交/手续费列")
        report = out_root / "performance" / f"performance_report_{cfg.run_id}.xlsx"
        ok(report.exists(), "产出 performance_report xlsx")
        sig_path = out_root / "signals" / f"signals_{cfg.run_id}.csv"
        ok(sig_path.exists(), "产出 signals/")
        sig = pd.read_csv(sig_path)
        ok(not sig.empty and "signal_kind" in sig.columns, "signals 非空且含 signal_kind")


def test_portfolio_sum_pnl_mean_pct():
    """组合：日盈亏=实例相加，日收益率=fillna(0) 均值（总本金 N×capital）。"""
    import pandas as pd
    pnl1 = pd.Series([1000.0, 1000.0], index=pd.to_datetime(["2021-01-04", "2021-01-05"]))
    pnl2 = pd.Series([2000.0], index=pd.to_datetime(["2021-01-05"]))
    pct1 = pd.Series([0.001, 0.001], index=pnl1.index)
    pct2 = pd.Series([0.002], index=pnl2.index)
    pnl_combined = pd.concat([pnl1.rename("a"), pnl2.rename("b")], axis=1)
    pct_combined = pd.concat([pct1.rename("a"), pct2.rename("b")], axis=1)
    daily_pnl = pnl_combined.fillna(0).sum(axis=1)
    daily_pct = pct_combined.fillna(0).mean(axis=1)
    ok(abs(daily_pnl.iloc[0] - 1000.0) < 1e-9, "首日仅 a 活跃：日盈亏=a")
    ok(abs(daily_pnl.iloc[1] - 3000.0) < 1e-9, "次日 a+b 活跃：日盈亏=a+b")
    ok(abs(daily_pct.iloc[0] - 0.0005) < 1e-9, "首日组合收益率=a/2")
    ok(abs(daily_pnl.iloc[0] - daily_pct.iloc[0] * 2_000_000) < 1e-6,
       "日盈亏=日收益率×(N×capital)")
    ok(pct_combined.mean(axis=1).iloc[0] > 0.0009,
       "skipna 会高估首日收益率（仅活跃实例）")


if __name__ == "__main__":
    test_bar_converter()
    test_reconcile_sim_open_long()
    test_reconcile_open_dedup()
    test_reconcile_bid_ask_open_long()
    test_reconcile_trade_mode()
    test_ref_leg_close_median()
    test_spread_accounting_bar_start_mtm()
    test_reconcile_open_reset_on_flat_transition()
    test_reconcile_close_no_refill()
    test_reconcile_close_requires_position()
    test_load_backtest_config()
    test_run_instance_smoke()
    test_parallel_equals_sequential()
    test_signal_recorder_dedup_and_schema()
    test_write_outputs_files()
    test_portfolio_sum_pnl_mean_pct()
    print("ALL BACKTEST RUNNER TESTS PASSED")
