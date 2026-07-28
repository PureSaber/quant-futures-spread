"""backtest/io/config_loader.py — 加载 strategies.yaml + backtest.yaml。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from utils.strategy_registry import expand_registry

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CALENDAR_MODES = frozenset({"calendar_dom_sub", "tenor_select"})
DEFAULT_DOM_TABLE = "/Volumes/JR/数据文档/商品期货-主力合约"


@dataclass
class BacktestConfig:
    run_id: str
    strategies: list[dict]
    data_dir: str
    years: list[str]
    future_list_path: str
    products: list[str]
    exclude: list[str]
    use_trade_flag: bool
    params_portfolio: list[int]
    output_dir: str
    capital: float
    source_type: str = "csv_spread"
    universe_mode: str = "manual"
    dom_table_dir: str = ""
    tenor: str = "dom_sub"
    roll_on_switch: str = "defer_until_flat"
    auto_universe: bool = False
    catalog_keys: list[str] = field(default_factory=list)
    jobs: int = 1
    raw: dict[str, Any] = field(default_factory=dict)

    def is_calendar_mode(self) -> bool:
        return self.universe_mode in CALENDAR_MODES


def _abs(path: str) -> str:
    p = Path(path)
    return str(p if p.is_absolute() else REPO_ROOT / p)


def load_backtest_config(path: str, config_dir: str | None = None) -> BacktestConfig:
    cfg_path = Path(path)
    if not cfg_path.is_absolute():
        cfg_path = REPO_ROOT / cfg_path
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cdir = Path(config_dir) if config_dir else cfg_path.parent
    if not cdir.is_absolute():
        cdir = REPO_ROOT / cdir

    registry = yaml.safe_load((cdir / "strategies.yaml").read_text(encoding="utf-8") or "{}")
    strategies = expand_registry(list(registry.get("strategies") or []), cdir)

    data = raw.get("data") or {}
    universe = raw.get("universe") or {}
    roll = raw.get("roll") or {}
    output = raw.get("output") or {}
    capital = float(raw.get("capital", 1_000_000))

    if universe.get("auto"):
        raise ValueError(
            "universe.auto 已废弃。请改用 universe.mode: calendar_dom_sub 或 tenor_select，"
            "见 docs/universe设计.md"
        )

    mode = str(universe.get("mode") or "manual").strip()
    if mode not in ("manual", "calendar_dom_sub", "tenor_select"):
        raise ValueError(f"未知 universe.mode: {mode!r}")

    tenor = str(universe.get("tenor") or "dom_sub").strip()
    if mode == "calendar_dom_sub":
        tenor = "dom_sub"

    dom_dir = str(universe.get("dom_table_dir") or DEFAULT_DOM_TABLE)

    roll_on = str(roll.get("on_switch") or "defer_until_flat").strip()
    if mode in CALENDAR_MODES and roll_on != "defer_until_flat":
        raise ValueError(
            f"calendar 模式仅支持 roll.on_switch: defer_until_flat，当前为 {roll_on!r}。"
            "见 docs/universe设计.md"
        )

    return BacktestConfig(
        run_id=str(raw.get("run_id", "backtest")),
        strategies=strategies,
        data_dir=str(data.get("data_dir", "")),
        years=[str(y) for y in data.get("years", [])],
        future_list_path=_abs(data.get("future_list", "config/future_list.csv")),
        products=list(universe.get("products") or []),
        exclude=list(universe.get("exclude") or []),
        use_trade_flag=bool(universe.get("use_trade_flag", False)),
        params_portfolio=list(roll.get("params_portforlio", roll.get("params_portfolio", [3, 3]))),
        output_dir=_abs(output.get("dir", "output")),
        capital=capital,
        source_type=str(data.get("source", "csv_spread")),
        universe_mode=mode,
        dom_table_dir=dom_dir,
        tenor=tenor,
        roll_on_switch=roll_on,
        auto_universe=False,
        catalog_keys=list(universe.get("catalog_keys") or []),
        jobs=max(1, int(raw.get("jobs", 1) or 1)),
        raw=raw,
    )
