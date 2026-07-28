"""tests/test_example_strategies.py — example_dom_sub / example_cross_product 团队示例。"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from core.io.config_loader import BacktestConfig, load_backtest_config
from core.engine.runner import run_backtest, run_instance
from core.types import BarData
from data_sources.csv_catalog import CsvCatalogSource
from framework.base import OPEN_LONG
from utils.contract_util import FutureList, catalog_key_of

_ROOT = Path(__file__).resolve().parent.parent
_SYM_DOM = "A2103&A2105"
_SYM_X = "A2003&B2003"


def ok(cond, msg=""):
    assert cond, msg


class FakePos:
    def __init__(self, long_qty=0.0, short_qty=0.0):
        self.long_qty = long_qty
        self.short_qty = short_qty
        self.long_avg = 0.0
        self.short_avg = 0.0


class FakeCtx:
    def __init__(self, closes, pos=None, tick=1.0):
        self._closes = list(closes)
        self._pos = pos or FakePos()
        self._tick = tick
        self.logs: list[str] = []

    def get_bars(self, symbol, source="comb", interval="1m", limit=240, exchange=None):
        n = len(self._closes)
        start = max(0, n - limit)
        return [
            {"close_price": self._closes[i], "trading_day": "2020-01-02"}
            for i in range(start, n)
        ]

    def get_position(self, symbol):
        return self._pos

    def price_tick_of(self, symbol):
        return self._tick

    def log(self, msg):
        self.logs.append(msg)

    def log_warning(self, msg):
        pass

    def log_debug(self, msg):
        pass


def _minute_df(n: int, base: float = 100.0, amp: float = 5.0) -> pd.DataFrame:
    t0 = datetime(2020, 1, 3, 9, 0)
    rows = []
    for i in range(n):
        dt = t0 + timedelta(minutes=i)
        c = base + amp * ((i % 20) - 10) / 10.0
        rows.append({
            "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "tradingday": "2020-01-03",
            "open": c, "high": c + 1, "low": c - 1, "close": c,
            "close_x": 3000.0, "close_y": 2900.0, "trade": True,
        })
    return pd.DataFrame(rows)


def _write_dom_catalog(root: Path, spread: str) -> Path:
    data_dir = root / "data"
    cat = data_dir / "ContractCatalog"
    md = data_dir / "MarketData" / "2020" / "A"
    cat.mkdir(parents=True)
    md.mkdir(parents=True)
    leg0, leg1 = spread.split("&")
    pd.DataFrame([{
        "symbol": spread, "symbol_0": leg0, "symbol_1": leg1, "year": 202006,
    }]).to_csv(cat / "A_symbol_list.csv", index=False)
    _minute_df(500).to_csv(md / f"{spread}.csv", index=False)
    return data_dir


def _write_cross_catalog(root: Path, spread: str) -> Path:
    data_dir = root / "data"
    key = catalog_key_of(spread)
    cat = data_dir / "ContractCatalog"
    md = data_dir / "MarketData" / "2020" / key
    cat.mkdir(parents=True)
    md.mkdir(parents=True)
    leg0, leg1 = spread.split("&")
    pd.DataFrame([{
        "symbol": f"{leg0}&{leg0}", "symbol_0": leg0, "symbol_1": leg1, "year": 202003,
    }]).to_csv(cat / f"{key}_symbol_list.csv", index=False)
    _minute_df(500).to_csv(md / f"{spread}.csv", index=False)
    return data_dir


def test_dom_sub_load_and_on_bar():
    from strategies.example_dom_sub.strategy import Strategy

    closes = [100.0] * 59 + [90.0]
    strat = Strategy("ex", {"symbol": _SYM_DOM, "lookback": 60, "entry_z": 1.0, "vol_per_layer": [1]}, FakeCtx(closes))
    bar = BarData(
        symbol=_SYM_DOM, exchange="DCE", datetime=datetime(2020, 1, 3, 10, 0),
        open_price=90, high_price=91, low_price=89, close_price=90,
        volume=0, trading_day="2020-01-03", product_id="A",
    )
    targets = strat.on_bar(bar)
    ok(targets and targets[0].side == OPEN_LONG, "z 过低开多")


def test_examples_skip_panel():
    from core.panel.sector_panel import strategy_needs_panel

    ok(not strategy_needs_panel("strategies.example_dom_sub.strategy"))
    ok(not strategy_needs_panel("strategies.example_cross_product.strategy"))


def test_dom_sub_on_symbol_switch():
    from strategies.example_dom_sub.strategy import Strategy

    ctx = FakeCtx([100.0] * 80)
    strat = Strategy("ex", {"symbol": _SYM_DOM, "product": "A", "vol_per_layer": [1]}, ctx)
    strat.on_init()
    ok(any("product=A" in m for m in ctx.logs), "on_init 记录 product")
    ctx.logs.clear()
    strat.on_symbol_switch(_SYM_DOM, "A2105&A2109")
    ok(strat.symbol == "A2105&A2109", "symbol 更新")
    ok(any("换月" in m for m in ctx.logs), "记录换月日志")


def test_cross_product_requires_symbol():
    from strategies.example_cross_product.strategy import Strategy

    try:
        Strategy("ex", {}, FakeCtx([100.0]))
        ok(False, "应拒绝无 symbol")
    except ValueError:
        ok(True, "无 symbol 抛错")


def test_cross_product_catalog_key():
    ok(catalog_key_of(_SYM_X) == "A&B", "跨品种 key")


def test_config_loader_example_entries():
    cfg_dom = load_backtest_config("config/backtest_example_dom_sub.yaml")
    ok(cfg_dom.universe_mode == "calendar_dom_sub", "dom_sub calendar")
    ok(cfg_dom.strategies[0]["module"] == "strategies.example_dom_sub.strategy", "dom 模块")

    cfg_x = load_backtest_config("config/backtest_example_cross_product.yaml")
    ok(cfg_x.universe_mode == "manual", "cross manual")
    syms = [e["params"]["symbol"] for e in cfg_x.strategies]
    ok("A2003&B2003" in syms and len(syms) == 3, f"展开 3 实例 got {syms}")


def test_run_instance_smoke_cross_product():
    td = tempfile.mkdtemp()
    data_dir = _write_cross_catalog(Path(td), _SYM_X)
    fl_path = _ROOT / "config" / "future_list.csv"
    entry = {
        "id": "x_smoke",
        "module": "strategies.example_cross_product.strategy",
        "enabled": True,
        "params": {
            "symbol": _SYM_X,
            "source": "comb",
            "lookback": 30,
            "entry_z": 0.5,
            "vol_per_layer": [1],
        },
    }
    cfg = BacktestConfig(
        run_id="x_smoke",
        strategies=[entry],
        data_dir=str(data_dir),
        years=["2020"],
        future_list_path=str(fl_path),
        products=["A", "B"],
        exclude=[],
        use_trade_flag=False,
        output_dir=str(Path(td) / "out"),
        capital=1_000_000,
        source_type="csv_catalog",
    )
    fl = FutureList.load(str(fl_path))
    source = CsvCatalogSource(str(data_dir))
    res = run_instance(entry, cfg, fl, source)
    ok(len(res.daily_ret) >= 1, "cross 实例产出日收益")


def test_run_backtest_calendar_dom_sub_example():
    td = tempfile.mkdtemp()
    tmp = Path(td)
    dom_dir = tmp / "dom"
    dom_dir.mkdir()
    pd.DataFrame([
        {"tradingday": "2020-01-03", "product": "A", "contract_type": "主力", "contract": "A2103"},
        {"tradingday": "2020-01-03", "product": "A", "contract_type": "次主力", "contract": "A2105"},
    ]).to_csv(dom_dir / "主力合约表-2020.csv", index=False)
    data_dir = _write_dom_catalog(tmp, _SYM_DOM)
    fl_path = _ROOT / "config" / "future_list.csv"
    cfg = BacktestConfig(
        run_id="dom_ex",
        strategies=[{
            "id": "example_dom_sub",
            "module": "strategies.example_dom_sub.strategy",
            "enabled": True,
            "params": {"source": "comb", "lookback": 30, "entry_z": 0.5, "vol_per_layer": [1]},
        }],
        data_dir=str(data_dir),
        years=["2020"],
        future_list_path=str(fl_path),
        products=["A"],
        exclude=[],
        use_trade_flag=False,
        output_dir=str(tmp / "out"),
        capital=1_000_000,
        source_type="csv_catalog",
        universe_mode="calendar_dom_sub",
        dom_table_dir=str(dom_dir),
        tenor="dom_sub",
        roll_on_switch="defer_until_flat",
    )
    res = run_backtest(cfg)
    ok(any(k.endswith("__A") for k in res.instances), "calendar 展开 example_dom_sub__A")
    ok(len(res.instances) >= 1, "至少 1 实例跑完")


if __name__ == "__main__":
    test_dom_sub_load_and_on_bar()
    test_examples_skip_panel()
    test_dom_sub_on_symbol_switch()
    test_cross_product_requires_symbol()
    test_cross_product_catalog_key()
    test_config_loader_example_entries()
    test_run_instance_smoke_cross_product()
    test_run_backtest_calendar_dom_sub_example()
    print("test_example_strategies: OK")
