"""core/engine/runner.py —— 基于 FuturesSpread 契约的回测主循环。"""
from __future__ import annotations

import importlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace

import pandas as pd

from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from framework.base import TargetOrder, DirectSignal
from utils.contract_util import FutureList, product_of, split_spread, catalog_key_of
from data_sources import build_source
from backtest.data.universe import expand_calendar_universe
from backtest.data.dominant_calendar import DominantCalendar
from backtest.data.spread_schedule import cal_map_for, unique_spreads_from_cal, SpreadSchedule
from backtest.engine.roll_governor import RollGovernor
from utils.dominant_contract import normalize_tenor
from backtest.data.bar_converter import df_to_bars
from backtest.data.market_store import InMemoryMarketStore
from backtest.engine.context import BacktestStrategyContext
from backtest.engine.reconcile_sim import ReconcileSimulator
from backtest.engine.signal_recorder import SignalRecorder, strategy_short_name
from backtest.io.config_loader import BacktestConfig
from backtest.panel.sector_panel import (
    build_panel_from_entries, build_panel_from_schedule, strategy_needs_panel,
)
from backtest.portfolio.accounting import SpreadAccounting
from backtest.portfolio.commission import (
    SpreadOpenDayTracker, commission_for_trade, leg_products,
)
from backtest.portfolio.position_book import BacktestPositionBook
from backtest.portfolio.sizing import compute_vol_per_layer, ref_leg_close_from_df
from backtest.types import BarData
from utils.logger import get_logger
from utils.panel_registry import get_panel, register_panel

logger = get_logger("BacktestRunner")


@dataclass
class InstanceResult:
    """单实例回测产出：日度汇总 + 价差成交流水。"""
    instance_id: str
    spread: str
    product: str
    daily: pd.DataFrame              # index=tradingday, 见 SpreadAccounting.daily_frame
    fills: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    roll_events: list[dict] = field(default_factory=list)

    @property
    def daily_ret(self) -> pd.Series:
        if self.daily is None or self.daily.empty:
            return pd.Series(dtype="float64")
        return self.daily["daily_pnl_pct"]


def _empty_instance(sid: str = "", spread: str = "", product: str = "") -> InstanceResult:
    return InstanceResult(sid, spread, product, pd.DataFrame(columns=SpreadAccounting._DAILY_COLS))


@dataclass
class BacktestResult:
    instances: dict[str, InstanceResult] = field(default_factory=dict)
    portfolio_daily: pd.Series | None = None
    roll_events: list[dict] = field(default_factory=list)



def _resolve_tenor(entry: dict, cfg: BacktestConfig) -> str:
    params = entry.get("params") or {}
    return normalize_tenor(params.get("tenor") or cfg.tenor)


def _seed_market_history(
    market: InMemoryMarketStore,
    df: pd.DataFrame,
    symbol: str,
    exchange: str,
    product: str,
    source: str,
    before_dt,
    max_bars: int = 5000,
) -> None:
    """换 spread 前灌入历史 bar，供 get_bars warmup。"""
    if df is None or df.empty:
        return
    sub = df
    if before_dt is not None and "datetime" in df.columns:
        sub = df[pd.to_datetime(df["datetime"]) < pd.Timestamp(before_dt)]
    if sub.empty:
        return
    tail = sub.tail(max_bars)
    for bar in df_to_bars(tail, symbol, exchange, product_id=product, source=source):
        market.append(bar)


def _apply_symbol_switch(
    strat,
    sim: ReconcileSimulator,
    sid: str,
    old_sym: str,
    new_sym: str,
    ctx: BacktestStrategyContext | None,
    exchange: str,
    tick_size: float,
    market: InMemoryMarketStore | None,
    spread_dfs: dict[str, pd.DataFrame] | None,
    product: str,
    comb_source: str,
    bar_dt=None,
    comm_tracker: SpreadOpenDayTracker | None = None,
) -> None:
    if not new_sym or old_sym == new_sym:
        return
    sim.clear_symbol(sid, old_sym)
    sim.clear_symbol(sid, new_sym)
    if comm_tracker is not None:
        comm_tracker.clear_symbol(sid, old_sym)
        comm_tracker.clear_symbol(sid, new_sym)
    if ctx is not None:
        ctx.register_symbol(new_sym, exchange, tick_size)
    if market is not None and spread_dfs and new_sym in spread_dfs:
        _seed_market_history(
            market, spread_dfs[new_sym], new_sym, exchange, product, comb_source, bar_dt,
        )
    if market is not None and old_sym and old_sym != new_sym:
        market.clear_symbol(old_sym, exchange, "1m", comb_source)
    try:
        strat.on_symbol_switch(old_sym, new_sym)
    except Exception:  # noqa: BLE001
        logger.exception(f"on_symbol_switch 异常 id={sid} {old_sym} -> {new_sym}")
        strat.symbol = new_sym
        strat.params["symbol"] = new_sym


def _run_bar_loop(
    sid: str, strat, sim: ReconcileSimulator, book: BacktestPositionBook,
    acct: SpreadAccounting, market: InMemoryMarketStore,
    bars: list[BarData], symbol: str,
    fl: FutureList, comm_tracker: SpreadOpenDayTracker,
    governor: RollGovernor | None, cal_by_day: dict[str, str] | None,
    sig_rec: SignalRecorder,
    ctx: BacktestStrategyContext | None = None,
    exchange: str = "", tick_size: float = 0.0,
    spread_dfs: dict[str, pd.DataFrame] | None = None,
    product: str = "", comb_source: str = "comb",
) -> list[dict]:
    fills: list[dict] = []
    for bar in bars:
        eff = symbol
        if governor is not None and cal_by_day is not None:
            cal_sym = cal_by_day.get(bar.trading_day) or governor.trade_symbol
            pos_pre = book.get_strategy_position(sid, governor.trade_symbol)
            net_pre = acct.net_qty(pos_pre.long_qty, pos_pre.short_qty)
            eff = governor.effective_symbol(cal_sym, net_pre, bar.trading_day)
            if bar.symbol != eff:
                continue
            if eff != strat.symbol:
                _apply_symbol_switch(
                    strat, sim, sid, strat.symbol, eff, ctx, exchange, tick_size,
                    market, spread_dfs, product, comb_source, bar.datetime, comm_tracker,
                )

        sim.reset_opens_if_flat(sid, eff, book)
        market.append(bar)

        try:
            targets = strat.on_bar(bar) or []
        except Exception:  # noqa: BLE001
            logger.exception(f"on_bar 异常 id={sid}")
            targets = []

        if targets and all(isinstance(x, DirectSignal) for x in targets):
            logger.warning(f"DirectSignal 策略 {sid} 回测暂不支持")
            targets = []
        targets = [x for x in targets if isinstance(x, TargetOrder)]
        if governor is not None:
            targets = governor.filter_targets(targets)

        if targets:
            sig_rec.record_targets(eff, targets, bar)
        sim.reconcile(sid, eff, targets, strat.trade_mode)
        pos = book.get_strategy_position(sid, eff)
        net_start = acct.net_qty(pos.long_qty, pos.short_qty)
        leg0_p, leg1_p = leg_products(eff)
        bar_commission, trades, trade_comms = _process_bar_fills(
            strat, sim, book, acct, bar, fl, comm_tracker,
            leg0_p, leg1_p, eff, sig_rec, governor,
        )
        inbar = SpreadAccounting.inbar_pnl(trades, bar.close_price, acct.point)
        acct.mark_bar(
            bar.trading_day, bar.close_price, net_start, bar_commission, inbar,
            n_trades=len(trades),
            n_win_trades=SpreadAccounting.count_win_trades(trades, bar.close_price, acct.point),
        )
        for tr, tr_comm in zip(trades, trade_comms):
            fills.append({
                "instance_id": sid, "spread": eff,
                "datetime": tr.datetime, "trading_day": tr.trading_day,
                "direction": tr.direction, "offset": tr.offset,
                "price": float(tr.price), "volume": float(tr.volume),
                "commission": float(tr_comm),
            })

        if governor is not None and cal_by_day is not None:
            cal_sym = cal_by_day.get(bar.trading_day) or governor.trade_symbol
            pos_after = book.get_strategy_position(sid, governor.trade_symbol)
            net_after = acct.net_qty(pos_after.long_qty, pos_after.short_qty)
            governor.after_bar(cal_sym, net_after, bar.trading_day)

    return fills


def _process_bar_fills(
    strat, sim: ReconcileSimulator, book: BacktestPositionBook,
    acct: SpreadAccounting, bar: BarData,
    fl: FutureList, comm_tracker: SpreadOpenDayTracker,
    leg0_product: str, leg1_product: str, symbol: str,
    recorder: SignalRecorder | None = None,
    governor: RollGovernor | None = None,
) -> tuple[float, list, list[float]]:
    """单根 bar：撮合当前挂单一次；on_trade 只更新挂单，留待后续 bar 成交。"""
    commission = 0.0
    trade_comms: list[float] = []
    trades = sim.try_fill(bar, book)
    sid = strat.strategy_id
    for tr in trades:
        pos = book.get_strategy_position(tr.strategy_id, tr.symbol)
        net_before = acct.net_qty(pos.long_qty, pos.short_qty)
        is_close_today = comm_tracker.is_close_today(
            sid, symbol, tr.trading_day, tr.offset,
        )
        tr_comm = commission_for_trade(
            fl, leg0_product, leg1_product, bar, tr.offset, tr.volume,
            is_close_today=is_close_today,
        )
        commission += tr_comm
        trade_comms.append(tr_comm)
        book.apply_trade(tr.strategy_id, tr.symbol, tr.direction, tr.offset,
                         tr.volume, tr.price)
        pos = book.get_strategy_position(tr.strategy_id, tr.symbol)
        net_after = acct.net_qty(pos.long_qty, pos.short_qty)
        comm_tracker.after_trade(sid, symbol, net_before, net_after, tr.trading_day)
        _dispatch_on_trade(strat, sim, bar, tr, recorder, governor)
    return commission, trades, trade_comms


def _dispatch_on_trade(strat, sim: ReconcileSimulator, bar: BarData, trade,
                       recorder: SignalRecorder | None = None,
                       governor: RollGovernor | None = None) -> None:
    try:
        new_targets = strat.on_trade(trade)
    except Exception:  # noqa: BLE001
        logger.exception(f"on_trade 异常 id={strat.strategy_id}")
        return
    if not isinstance(new_targets, list):
        return
    if new_targets and all(isinstance(x, DirectSignal) for x in new_targets):
        logger.warning(f"策略 {strat.strategy_id} 产出 DirectSignal，回测暂不支持，已跳过")
        return
    targets = [x for x in new_targets if isinstance(x, TargetOrder)]
    if governor is not None:
        targets = governor.filter_targets(targets)
    if recorder is not None and targets:
        recorder.record_targets(strat.symbol, targets, bar)
    sim.reconcile(strat.strategy_id, strat.symbol, targets, strat.trade_mode)


def run_instance(
    entry: dict, cfg: BacktestConfig, fl: FutureList, source,
) -> InstanceResult:
    sid = str(entry["id"])
    if not entry.get("enabled", True):
        return _empty_instance(sid)

    module_path = str(entry["module"])
    params = dict(entry.get("params") or {})
    params["run_id"] = str(cfg.run_id)
    symbol = str(params.get("symbol") or "").strip()
    if not symbol:
        logger.error(f"实例 {sid} 缺少 symbol，跳过")
        return _empty_instance(sid)

    leg0, leg1 = split_spread(symbol)
    product = product_of(leg0)        # 价差参考品种（point/tick/exchange/盯市口径）
    key = catalog_key_of(symbol)      # 数据集"品种键"（跨期 key==product，兼容 csv_spread）
    if cfg.products and product not in cfg.products:
        return _empty_instance(sid, symbol, product)
    if product in cfg.exclude:
        return _empty_instance(sid, symbol, product)

    df = source.load_spread(key, symbol, cfg.years)
    if df.empty:
        logger.warning(f"无数据 key={key} spread={symbol}")
        return _empty_instance(sid, symbol, product)

    try:
        exchange = str(fl.get(product, "exchange")).upper()
    except Exception:  # noqa: BLE001
        exchange = "DCE"
    tick_map = {symbol.lower(): float(fl.tick_size(product))}
    ex_map = {symbol.lower(): exchange}
    comb_source = str(params.get("source", "comb"))

    bars = df_to_bars(df, symbol, exchange, product_id=product, source=comb_source)
    if not bars:
        logger.warning(f"无 bar spread={symbol}")
        return _empty_instance(sid, symbol, product)

    market = InMemoryMarketStore()
    book = BacktestPositionBook()
    ctx = BacktestStrategyContext(sid, market, book, ex_map, tick_map)
    sim = ReconcileSimulator()

    mod = importlib.import_module(module_path)
    strat = mod.Strategy(sid, params, ctx)
    point = float(fl.point(product))
    capital = float(params.get("capital", cfg.capital))
    acct = SpreadAccounting(point, capital)
    comm_tracker = SpreadOpenDayTracker()
    leg0_p, leg1_p = leg_products(symbol)

    strat.on_init()
    strat.on_start()

    cap_layers = params.get("capital_per_layer")
    if cap_layers and not params.get("vol_per_layer"):
        leg_px = ref_leg_close_from_df(df)
        if leg_px:
            strat.on_sizing(compute_vol_per_layer(leg_px, point, list(cap_layers)))
    elif params.get("vol_per_layer"):
        strat.on_sizing([float(v) for v in params["vol_per_layer"]])

    fills: list[dict] = []
    sig_rec = SignalRecorder(strategy_short_name(module_path))
    for bar in bars:
        sim.reset_opens_if_flat(sid, symbol, book)
        market.append(bar)

        try:
            targets = strat.on_bar(bar) or []
        except Exception:  # noqa: BLE001
            logger.exception(f"on_bar 异常 id={sid}")
            targets = []

        if targets and all(isinstance(x, DirectSignal) for x in targets):
            logger.warning(f"DirectSignal 策略 {sid} 回测暂不支持")
            targets = []
        targets = [x for x in targets if isinstance(x, TargetOrder)]

        if targets:
            sig_rec.record_targets(symbol, targets, bar)
        sim.reconcile(sid, symbol, targets, strat.trade_mode)
        pos = book.get_strategy_position(sid, symbol)
        net_start = acct.net_qty(pos.long_qty, pos.short_qty)
        bar_commission, trades, trade_comms = _process_bar_fills(
            strat, sim, book, acct, bar, fl, comm_tracker,
            leg0_p, leg1_p, symbol, sig_rec,
        )
        inbar = SpreadAccounting.inbar_pnl(trades, bar.close_price, point)
        acct.mark_bar(
            bar.trading_day, bar.close_price, net_start, bar_commission, inbar,
            n_trades=len(trades),
            n_win_trades=SpreadAccounting.count_win_trades(
                trades, bar.close_price, point),
        )
        for tr, tr_comm in zip(trades, trade_comms):
            fills.append({
                "instance_id": sid, "spread": symbol,
                "datetime": tr.datetime, "trading_day": tr.trading_day,
                "direction": tr.direction, "offset": tr.offset,
                "price": float(tr.price), "volume": float(tr.volume),
                "commission": float(tr_comm),
            })

    strat.on_stop()
    return InstanceResult(sid, symbol, product, acct.daily_frame(), fills, sig_rec.to_records())


def run_product_instance(
    entry: dict, cfg: BacktestConfig, fl: FutureList, source,
    calendar: DominantCalendar,
) -> InstanceResult:
    sid = str(entry["id"])
    if not entry.get("enabled", True):
        return _empty_instance(sid)

    module_path = str(entry["module"])
    params = dict(entry.get("params") or {})
    params["run_id"] = str(cfg.run_id)
    product = str(params.get("product") or "").strip().upper()
    if not product:
        logger.error(f"实例 {sid} 缺少 product，跳过")
        return _empty_instance(sid)

    if cfg.products and product not in cfg.products:
        return _empty_instance(sid, "", product)
    if product in cfg.exclude:
        return _empty_instance(sid, "", product)

    tenor = _resolve_tenor(entry, cfg)
    cal_by_day = cal_map_for(calendar, product, cfg.years, tenor)
    if not cal_by_day:
        logger.warning(f"日程表无交易日 product={product} tenor={tenor}")
        return _empty_instance(sid, "", product)

    trading_days = sorted(cal_by_day.keys())
    initial_sym = cal_by_day[trading_days[0]]

    spread_dfs: dict[str, pd.DataFrame] = {}
    all_bars: list[BarData] = []
    try:
        exchange = str(fl.get(product, "exchange")).upper()
    except Exception:  # noqa: BLE001
        exchange = "DCE"
    comb_source = str(params.get("source", "comb"))

    for sym in unique_spreads_from_cal(cal_by_day):
        df = source.load_spread(product, sym, cfg.years)
        if df.empty:
            logger.warning(f"无数据 product={product} spread={sym}")
            continue
        spread_dfs[sym] = df
        all_bars.extend(
            df_to_bars(df, sym, exchange, product_id=product, source=comb_source)
        )

    if not all_bars:
        return _empty_instance(sid, initial_sym, product)

    all_bars.sort(key=lambda b: (str(b.trading_day), b.datetime))

    governor = RollGovernor(trade_symbol=initial_sym)
    tick_size = float(fl.tick_size(product))
    tick_map = {sym.lower(): tick_size for sym in spread_dfs}
    ex_map = {sym.lower(): exchange for sym in spread_dfs}
    market = InMemoryMarketStore()
    book = BacktestPositionBook()
    ctx = BacktestStrategyContext(sid, market, book, ex_map, tick_map)
    sim = ReconcileSimulator()

    params["symbol"] = initial_sym
    mod = importlib.import_module(module_path)
    strat = mod.Strategy(sid, params, ctx)
    point = float(fl.point(product))
    capital = float(params.get("capital", cfg.capital))
    acct = SpreadAccounting(point, capital)
    comm_tracker = SpreadOpenDayTracker()

    strat.on_init()
    strat.on_start()

    cap_layers = params.get("capital_per_layer")
    if cap_layers and not params.get("vol_per_layer"):
        first_df = spread_dfs.get(initial_sym, next(iter(spread_dfs.values())))
        leg_px = ref_leg_close_from_df(first_df)
        if leg_px:
            strat.on_sizing(compute_vol_per_layer(leg_px, point, list(cap_layers)))
    elif params.get("vol_per_layer"):
        strat.on_sizing([float(v) for v in params["vol_per_layer"]])

    sig_rec = SignalRecorder(strategy_short_name(module_path))
    fills = _run_bar_loop(
        sid, strat, sim, book, acct, market, all_bars, initial_sym,
        fl, comm_tracker, governor, cal_by_day, sig_rec, ctx, exchange, tick_size,
        spread_dfs, product, comb_source,
    )
    strat.on_stop()
    roll_ev = [
        {"instance_id": sid, "tradingday": e.tradingday, "old_symbol": e.old_symbol,
         "new_symbol": e.new_symbol, "reason": e.reason}
        for e in governor.events
    ]
    return InstanceResult(
        sid, governor.trade_symbol, product, acct.daily_frame(), fills, sig_rec.to_records(),
        roll_events=roll_ev,
    )


def _run_entry(entry: dict, cfg: BacktestConfig, fl: FutureList, source,
               calendar: DominantCalendar | None) -> InstanceResult:
    if cfg.is_calendar_mode() and calendar is not None:
        return run_product_instance(entry, cfg, fl, source, calendar)
    return run_instance(entry, cfg, fl, source)


# ── 多进程并行 ───────────────────────────────────────────────────────────
_WORKER_CTX: dict = {}


def _init_worker(cfg: BacktestConfig, calendar: DominantCalendar | None) -> None:
    import logging
    bootstrap_strategy_path()
    logging.getLogger("Strategy").setLevel(logging.WARNING)
    _WORKER_CTX["cfg"] = cfg
    _WORKER_CTX["fl"] = FutureList.load(cfg.future_list_path)
    _WORKER_CTX["source"] = build_source(cfg.source_type, {"data_dir": cfg.data_dir})
    _WORKER_CTX["calendar"] = calendar
    get_panel(cfg.run_id)


def _run_entry_worker(entry: dict) -> InstanceResult:
    return _run_entry(
        entry, _WORKER_CTX["cfg"], _WORKER_CTX["fl"],
        _WORKER_CTX["source"], _WORKER_CTX.get("calendar"),
    )


def _run_all_instances(
    cfg: BacktestConfig, fl: FutureList, source, calendar: DominantCalendar | None,
) -> list[InstanceResult]:
    entries = list(cfg.strategies)
    jobs = max(1, int(getattr(cfg, "jobs", 1) or 1))
    if jobs <= 1 or len(entries) <= 1:
        return [_run_entry(e, cfg, fl, source, calendar) for e in entries]
    worker_cfg = replace(cfg, strategies=[])
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker,
                             initargs=(worker_cfg, calendar)) as ex:
        return list(ex.map(_run_entry_worker, entries))


def _sector_maps(cfg: BacktestConfig, entries: list[dict]) -> tuple[dict, dict]:
    try:
        from strategies.zhouhaotian.sectors import SECTOR_MAP
        sector_map = dict(SECTOR_MAP)
    except Exception:  # noqa: BLE001
        sector_map = {}
    industry_map: dict = {}
    if any("gaozong2" in str(e.get("module", "")) for e in entries):
        try:
            from pathlib import Path
            from strategies.gaozong2.sectors import load_sector_map
            bk2_map, _, _ = load_sector_map(Path(cfg.future_list_path))
            industry_map = dict(bk2_map)
        except Exception:  # noqa: BLE001
            pass
    if not industry_map:
        try:
            from strategies.xuhe.sectors import INDUSTRY_MAP
            industry_map = dict(INDUSTRY_MAP)
        except Exception:  # noqa: BLE001
            pass
    return sector_map, industry_map


def _maybe_build_sector_panel(
    entries: list[dict], cfg: BacktestConfig, fl, source,
    schedule: SpreadSchedule | None,
):
    if get_panel(cfg.run_id) is not None:
        return
    if not any(strategy_needs_panel(str(e.get("module", ""))) for e in entries):
        return
    sector_map, industry_map = _sector_maps(cfg, entries)
    if cfg.is_calendar_mode() and schedule is not None:
        from backtest.data.universe import resolve_products
        products = resolve_products(
            cfg.products, schedule, cfg.years, fl, cfg.exclude, cfg.use_trade_flag,
        )
        panel = build_panel_from_schedule(
            schedule, products, cfg.years, cfg, fl, source,
            sector_map, industry_map=industry_map,
        )
    else:
        panel = build_panel_from_entries(
            entries, cfg, fl, source, sector_map, industry_map=industry_map,
        )
    if panel is not None:
        register_panel(cfg.run_id, panel)
        logger.info(f"SpreadSectorPanel 已注册 run_id={cfg.run_id}")


def run_backtest(cfg: BacktestConfig) -> BacktestResult:
    fl = FutureList.load(cfg.future_list_path)
    source = build_source(cfg.source_type, {"data_dir": cfg.data_dir})
    calendar: DominantCalendar | None = None
    schedule: SpreadSchedule | None = None

    if cfg.is_calendar_mode():
        calendar = DominantCalendar(cfg.dom_table_dir)
        schedule = SpreadSchedule.from_dir(cfg.dom_table_dir, cfg.tenor)
        cfg.strategies = expand_calendar_universe(
            cfg.strategies, schedule, cfg.products, cfg.years,
            cfg.exclude, fl, cfg.use_trade_flag,
        )
        cfg.products = list({
            str(e.get("params", {}).get("product", "")).upper()
            for e in cfg.strategies
            if e.get("params", {}).get("product")
        })
        logger.info(f"calendar 展开实例数={len(cfg.strategies)} default_tenor={cfg.tenor}")
    else:
        products = cfg.products
        if not products:
            products = fl.products()
            if cfg.use_trade_flag:
                products = [p for p in products if fl.is_tradable(p)]
        cfg.products = products

    entries = list(cfg.strategies)
    _maybe_build_sector_panel(entries, cfg, fl, source, schedule)

    result = BacktestResult()
    frames = []
    for ir in _run_all_instances(cfg, fl, source, calendar):
        if ir.daily is not None and not ir.daily.empty:
            result.instances[ir.instance_id] = ir
            frames.append(ir.daily_ret.rename(ir.instance_id))
        result.roll_events.extend(ir.roll_events)

    if frames:
        combined = pd.concat(frames, axis=1).sort_index()
        result.portfolio_daily = combined.fillna(0).mean(axis=1)
        result.portfolio_daily.name = "daily_return"
    return result
