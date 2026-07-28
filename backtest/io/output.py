"""backtest/io/output.py — 回测产出落盘。

产出：daily/portfolio、daily/symbol、trades、signals、performance。
"""
from __future__ import annotations

import os

import pandas as pd

from backtest.engine.runner import BacktestResult, InstanceResult
from backtest.engine.signal_recorder import write_signals_csv
from backtest.io.config_loader import BacktestConfig
from backtest.io.output_format import write_portfolio_csv, write_spread_csv
from backtest.io.output_format import write_summary_csv, write_trades_csv
from performance import summarize

try:
    from performance.report.generate import generate_report
except ImportError:
    generate_report = None  # type: ignore[misc, assignment]

_NAV_CORE_COLS = [
    "daily_pnl", "daily_pnl_pct", "commission", "num_trades", "win_trades",
]


def _strategy_name(cfg: BacktestConfig) -> str:
    if cfg.strategies:
        mod = str(cfg.strategies[0].get("module") or "")
        parts = mod.split(".")
        if len(parts) >= 2 and parts[0] == "strategies":
            return parts[1]
        sid = str(cfg.strategies[0].get("id", cfg.run_id))
        base = sid.split("__")[0]
        if base.endswith("_backtest"):
            return base[: -len("_backtest")]
        return base
    return cfg.run_id


def _active_instances(result: BacktestResult) -> dict[str, InstanceResult]:
    return {
        iid: ir for iid, ir in result.instances.items()
        if ir.daily is not None and not ir.daily.empty
    }


def _instance_nav(ir: InstanceResult) -> pd.DataFrame:
    out = ir.daily[[
        "daily_pnl", "daily_pnl_pct", "commission", "num_trades", "win_trades",
    ]].copy()
    out.index.name = "date"
    return out


def spread_nav_frame(result: BacktestResult, cfg: BacktestConfig) -> pd.DataFrame:
    """各套利对净值（对齐 CTA daily/symbol，一套利对一行/日）。"""
    strat = _strategy_name(cfg)
    parts: list[pd.DataFrame] = []
    for ir in _active_instances(result).values():
        nav = _instance_nav(ir).reset_index()
        nav.insert(1, "spread", ir.spread)
        nav.insert(2, "strategy", strat)
        parts.append(nav)
    if not parts:
        return pd.DataFrame(columns=["date", "spread", "strategy", *_NAV_CORE_COLS, "net_value"])
    return pd.concat(parts, ignore_index=True).sort_values(["date", "spread"])


def portfolio_nav_frame(result: BacktestResult, cfg: BacktestConfig) -> pd.DataFrame:
    """策略整体净值：日盈亏=各实例相加，日收益率=实例收益率均值（总本金≈N×capital）。"""
    active = _active_instances(result)
    if not active:
        return pd.DataFrame(columns=["date", "strategy", "num_spreads", *_NAV_CORE_COLS, "net_value"])

    strat = _strategy_name(cfg)
    if len(active) == 1:
        core = _instance_nav(next(iter(active.values())))
        num_spreads = pd.Series(1, index=core.index, dtype="int64")
    else:
        pnl_frames = pd.concat(
            [ir.daily["daily_pnl"].rename(iid) for iid, ir in active.items()],
            axis=1,
        ).sort_index()
        pct_frames = pd.concat(
            [ir.daily["daily_pnl_pct"].rename(iid) for iid, ir in active.items()],
            axis=1,
        ).sort_index()
        daily_pnl = pnl_frames.fillna(0).sum(axis=1)
        daily_pnl_pct = pct_frames.fillna(0).mean(axis=1)
        num_spreads = pct_frames.notna().sum(axis=1).astype(int)
        act = pd.concat([ir.daily[["commission", "num_trades", "win_trades"]]
                         for ir in active.values()])
        agg = act.groupby(level=0).sum().reindex(daily_pnl.index).fillna(0)
        core = pd.DataFrame({
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "commission": agg["commission"],
            "num_trades": agg["num_trades"].astype(int),
            "win_trades": agg["win_trades"].astype(int),
        })
        core.index.name = "date"

    out = core.reset_index()
    out.insert(1, "strategy", strat)
    out.insert(2, "num_spreads", num_spreads.reindex(core.index).fillna(0).astype(int).values)
    return out


def write_outputs(cfg: BacktestConfig, result: BacktestResult) -> str:
    out_root = os.path.join(cfg.output_dir, cfg.run_id)
    port_dir = os.path.join(out_root, "daily", "portfolio")
    sym_dir = os.path.join(out_root, "daily", "symbol")
    perf_dir = os.path.join(out_root, "performance")
    os.makedirs(port_dir, exist_ok=True)
    os.makedirs(sym_dir, exist_ok=True)
    os.makedirs(perf_dir, exist_ok=True)

    # 清理旧版 flat daily/*.csv
    for stale in (
        os.path.join(out_root, "daily", "daily_pnl.csv"),
        os.path.join(out_root, "daily", "instance.csv"),
        os.path.join(out_root, "daily", "portfolio.csv"),
    ):
        if os.path.isfile(stale):
            os.remove(stale)

    prefix = cfg.run_id
    port = portfolio_nav_frame(result, cfg)
    spread = spread_nav_frame(result, cfg)

    if not port.empty:
        write_portfolio_csv(port, os.path.join(port_dir, f"daily_pnl_portfolio_{prefix}.csv"))
    if not spread.empty:
        write_spread_csv(spread, os.path.join(sym_dir, f"daily_pnl_{prefix}.csv"))

    fills = [f for ir in result.instances.values() for f in ir.fills]
    if fills:
        trades_dir = os.path.join(out_root, "trades")
        os.makedirs(trades_dir, exist_ok=True)
        write_trades_csv(
            pd.DataFrame(fills).sort_values(["spread", "datetime"]),
            os.path.join(trades_dir, "trades.csv"),
        )

    signals = [s for ir in result.instances.values() for s in ir.signals]
    if signals:
        signals_dir = os.path.join(out_root, "signals")
        os.makedirs(signals_dir, exist_ok=True)
        write_signals_csv(
            pd.DataFrame(signals).sort_values(["strategy_id", "symbol", "action_datetime"]),
            os.path.join(signals_dir, f"signals_{prefix}.csv"),
        )

    if result.roll_events:
        rolls_dir = os.path.join(out_root, "rolls")
        os.makedirs(rolls_dir, exist_ok=True)
        pd.DataFrame(result.roll_events).to_csv(
            os.path.join(rolls_dir, "roll_events.csv"), index=False,
        )

    if not port.empty:
        s = summarize(port["daily_pnl_pct"], cfg.capital)
        s["num_trades"] = int(port["num_trades"].sum())
        s["total_commission"] = round(float(port["commission"].sum()), 2)
        write_summary_csv(
            pd.DataFrame([s], index=[cfg.run_id]),
            os.path.join(perf_dir, "summary.csv"),
        )

        if generate_report is not None:
            fills_df = pd.DataFrame(
                [f for ir in result.instances.values() for f in ir.fills],
            )
            report_path = os.path.join(
                perf_dir, f"performance_report_{prefix}.xlsx",
            )
            n_active = len(_active_instances(result))
            generate_report(
                spread_daily=spread,
                portfolio_daily=port,
                fills=fills_df if not fills_df.empty else None,
                output_path=report_path,
                capital=float(cfg.capital),
                strategy=_strategy_name(cfg),
                total_capital=float(cfg.capital) * max(n_active, 1),
            )

    return out_root
