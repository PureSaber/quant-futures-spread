"""Adapter from futures-spread results to quant-lab run schema v1."""

from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from quant_lab.contracts import RunManifest, write_standard_run

from core.engine.runner import BacktestResult
from core.io.config_loader import BacktestConfig


def _code_version(repo_root: Path) -> str:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()
    dirty = subprocess.run(["git", "diff", "--quiet"], cwd=repo_root, check=False).returncode != 0
    return f"{revision}+dirty" if dirty else revision


def _standard_returns(portfolio: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    if portfolio.empty:
        return pd.DataFrame()
    result = portfolio.copy()
    active_capital = float(cfg.capital) * result["num_spreads"].clip(lower=1)
    cost_return = result["commission"].astype(float) / active_capital
    result["net_return"] = result["daily_pnl_pct"].astype(float)
    result["gross_return"] = result["net_return"] + cost_return
    result["nav"] = (1 + result["net_return"]).cumprod()
    result["benchmark_return"] = np.nan
    return result[["date", "strategy", "gross_return", "net_return", "nav", "benchmark_return"]]


def _standard_positions_and_exposures(
    spread: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if spread.empty:
        return pd.DataFrame(), pd.DataFrame()
    rows: list[dict] = []
    exposures: list[dict] = []
    for date, group in spread.groupby("date", sort=True):
        weight = 1.0 / len(group)
        for _, item in group.iterrows():
            rows.append(
                {
                    "date": date,
                    "strategy": item["strategy"],
                    "symbol": item["spread"],
                    "quantity": np.nan,
                    "market_value": np.nan,
                    "weight": weight,
                    "side": "spread",
                }
            )
            exposures.append(
                {
                    "date": date,
                    "strategy": item["strategy"],
                    "exposure_type": "spread",
                    "name": item["spread"],
                    "value": weight,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(exposures)


def _standard_orders_and_costs(
    result: BacktestResult, strategy: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    fills = [fill for instance in result.instances.values() for fill in instance.fills]
    if not fills:
        return pd.DataFrame(), pd.DataFrame()
    fill_frame = pd.DataFrame(fills)
    orders = pd.DataFrame(
        {
            "timestamp": fill_frame["datetime"],
            "strategy": strategy,
            "symbol": fill_frame["spread"],
            "side": fill_frame["direction"].astype(str).str.lower(),
            "quantity": fill_frame["volume"],
            "target_weight": np.nan,
            "order_type": fill_frame["offset"].astype(str).str.lower(),
            "status": "simulated_filled",
        }
    )
    costs = pd.DataFrame(
        {
            "date": pd.to_datetime(fill_frame["trading_day"]),
            "strategy": strategy,
            "symbol": fill_frame["spread"],
            "commission": fill_frame["commission"].astype(float),
            "slippage": 0.0,
            "market_impact": 0.0,
            "borrow_cost": 0.0,
            "total_cost": fill_frame["commission"].astype(float),
        }
    )
    return orders, costs


def write_futures_standard_run(
    out_root: Path,
    cfg: BacktestConfig,
    result: BacktestResult,
    portfolio: pd.DataFrame,
    spread: pd.DataFrame,
    metrics: dict,
    strategy: str,
) -> RunManifest:
    positions, exposures = _standard_positions_and_exposures(spread)
    orders, costs = _standard_orders_and_costs(result, strategy)
    return write_standard_run(
        out_root,
        project="quant-futures-spread",
        run_id=cfg.run_id,
        strategy=strategy,
        frames={
            "returns": _standard_returns(portfolio, cfg),
            "positions": positions,
            "orders": orders,
            "costs": costs,
            "exposures": exposures,
        },
        metrics=metrics,
        config=asdict(cfg),
        code_version=_code_version(Path(__file__).resolve().parents[2]),
        dataset_snapshots={},
        tags={"asset_class": "cn_commodity_futures", "research_type": "spread"},
    )
