"""core/io/config_loader.py — 加载 strategies.yaml + backtest.yaml。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.paths import data_root, dom_table_dir, resolve_data_path
from utils.strategy_registry import expand_registry

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CALENDAR_MODES = frozenset({"calendar_dom_sub", "tenor_select"})


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
    output_dir: str
    capital: float
    source_type: str = "csv_spread"
    universe_mode: str = "manual"
    dom_table_dir: str = ""
    tenor: str = "dom_sub"
    roll_on_switch: str = "defer_until_flat"
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
    registry_list = list(registry.get("strategies") or [])
    strategy_filter = raw.get("strategy") or raw.get("strategies")
    if strategy_filter is not None:
        if isinstance(strategy_filter, str):
            ids = {strategy_filter.strip()}
        else:
            ids = {str(s).strip() for s in strategy_filter if str(s).strip()}
        registry_list = [e for e in registry_list if str(e.get("id") or "").strip() in ids]
        if not registry_list:
            raise ValueError(
                f"strategy {strategy_filter!r} 未在 {cdir / 'strategies.yaml'} 中注册"
            )
    strategies = expand_registry(registry_list, cdir)
    param_override = raw.get("strategy_params") or {}
    if param_override:
        sid = raw.get("strategy")
        for s in strategies:
            if sid is None or str(s.get("id") or "").strip() == str(sid).strip():
                s.setdefault("params", {})
                s["params"] = {**s["params"], **param_override}

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

    dom_dir = str(resolve_data_path(universe.get("dom_table_dir"), dom_table_dir()))

    roll_on = str(roll.get("on_switch") or "defer_until_flat").strip()
    if mode in CALENDAR_MODES and roll_on != "defer_until_flat":
        raise ValueError(
            f"calendar 模式仅支持 roll.on_switch: defer_until_flat，当前为 {roll_on!r}。"
            "见 docs/universe设计.md"
        )

    resolved_data_dir = str(resolve_data_path(data.get("data_dir"), data_root()))

    return BacktestConfig(
        run_id=str(raw.get("run_id", "backtest")),
        strategies=strategies,
        data_dir=resolved_data_dir,
        years=[str(y) for y in data.get("years", [])],
        future_list_path=_abs(data.get("future_list", "config/future_list.csv")),
        products=list(universe.get("products") or []),
        exclude=list(universe.get("exclude") or []),
        use_trade_flag=bool(universe.get("use_trade_flag", False)),
        output_dir=_abs(output.get("dir", "output")),
        capital=capital,
        source_type=str(data.get("source", "csv_spread")),
        universe_mode=mode,
        dom_table_dir=dom_dir,
        tenor=tenor,
        roll_on_switch=roll_on,
        jobs=max(1, int(raw.get("jobs", 1) or 1)),
        raw=raw,
    )
