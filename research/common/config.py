"""加载 factor mining YAML 配置。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.paths import cross_dir, data_root, dom_table_dir, resolve_data_path

_REPO = Path(__file__).resolve().parents[2]


@dataclass
class MiningConfig:
    round_id: str
    mode: str
    data_dir: Path
    cross_dir: Path
    dom_table_dir: Path
    years: list[str]
    calendar_products: list[str]
    cross_pairs: list[str]
    max_spreads_per_product: int
    horizons_min: list[int]
    lookback: int
    vol_short: int
    vol_long: int
    require_trade: bool
    max_eff_spread_ticks: float
    output_dir: Path
    min_abs_ic: float
    min_icir: float
    min_abs_realized_ic: float = 0.0
    min_realized_icir: float = 0.0
    max_corr_with_z_close: float = 1.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def round_output(self) -> Path:
        return self.output_dir / str(self.round_id)


def load_config(path: str | Path) -> MiningConfig:
    p = Path(path)
    if not p.is_absolute():
        p = _REPO / p
    with p.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    data = raw.get("data") or {}
    uni = raw.get("universe") or {}
    labels = raw.get("labels") or {}
    factors = raw.get("factors") or {}
    filters = raw.get("filters") or {}
    output = raw.get("output") or {}
    sel = raw.get("selection") or {}

    out_dir = Path(output.get("dir", "research/output"))
    if not out_dir.is_absolute():
        out_dir = _REPO / out_dir

    root = data_root()
    return MiningConfig(
        round_id=str(raw.get("round_id", "round_1")),
        mode=str(raw.get("mode", "full")),
        data_dir=resolve_data_path(data.get("data_dir"), root),
        cross_dir=resolve_data_path(data.get("cross_dir"), cross_dir()),
        dom_table_dir=resolve_data_path(data.get("dom_table_dir"), dom_table_dir()),
        years=[str(y) for y in (data.get("years") or ["2024"])],
        calendar_products=[str(x).upper() for x in (uni.get("calendar_products") or [])],
        cross_pairs=[str(x) for x in (uni.get("cross_pairs") or [])],
        max_spreads_per_product=int(uni.get("max_spreads_per_product") or 0),
        horizons_min=[int(h) for h in (labels.get("horizons_min") or [5, 15, 60, 240])],
        lookback=int(factors.get("lookback") or 60),
        vol_short=int(factors.get("vol_short") or 20),
        vol_long=int(factors.get("vol_long") or 120),
        require_trade=bool(filters.get("require_trade", True)),
        max_eff_spread_ticks=float(filters.get("max_eff_spread_ticks") or 50),
        output_dir=out_dir,
        min_abs_ic=float(sel.get("min_abs_ic") or 0.03),
        min_icir=float(sel.get("min_icir") or 0.5),
        min_abs_realized_ic=float(sel.get("min_abs_realized_ic") or 0.0),
        min_realized_icir=float(sel.get("min_realized_icir") or 0.0),
        max_corr_with_z_close=float(sel.get("max_corr_with_z_close") or 1.0),
        raw=raw,
    )
