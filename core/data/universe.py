"""core/data/universe.py — 实例展开（calendar / manual）。"""
from __future__ import annotations

from core.data.spread_schedule import SpreadSchedule
from utils.contract_util import FutureList


def expand_calendar_universe(
    strategies: list[dict],
    schedule: SpreadSchedule,
    products: list[str],
    years: list[str],
    exclude: list[str],
    fl: FutureList,
    use_trade_flag: bool,
) -> list[dict]:
    """每个 product × strategy 一条连续实例。"""
    prods = list(products)
    if not prods:
        prods = schedule.products_in_years(years)
    if use_trade_flag:
        prods = [p for p in prods if fl.is_tradable(p)]
    prods = [p for p in prods if p not in exclude]

    out: list[dict] = []
    for entry in strategies:
        if not entry.get("enabled", True):
            continue
        sid = str(entry["id"])
        base = dict(entry.get("params") or {})
        for prod in prods:
            out.append({
                "id": f"{sid}__{prod}",
                "module": str(entry["module"]),
                "enabled": True,
                "params": {**base, "product": prod},
            })
    return out


def resolve_products(
    configured: list[str],
    schedule: SpreadSchedule,
    years: list[str],
    fl: FutureList,
    exclude: list[str],
    use_trade_flag: bool,
) -> list[str]:
    prods = list(configured) if configured else schedule.products_in_years(years)
    if use_trade_flag:
        prods = [p for p in prods if fl.is_tradable(p)]
    return [p for p in prods if p not in exclude]
