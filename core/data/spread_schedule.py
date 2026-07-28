"""core/data/spread_schedule.py — product 日 spread 日程表。"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.data.dominant_calendar import DominantCalendar
from utils.dominant_contract import normalize_tenor


def cal_map_for(
    calendar: DominantCalendar,
    product: str,
    years: list[str] | None,
    tenor: str,
) -> dict[str, str]:
    """product 在 years 内每日 spread（过滤 None）。"""
    t = normalize_tenor(tenor)
    prod = str(product).upper()
    out: dict[str, str] = {}
    for td in calendar.trading_days(prod, years):
        sp = calendar.spread_of(prod, td, t)
        if sp:
            out[str(td)] = sp
    return out


def unique_spreads_from_cal(cal_map: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sp in cal_map.values():
        if sp not in seen:
            seen.add(sp)
            out.append(sp)
    return out


def calendar_by_products(
    calendar: DominantCalendar,
    products: list[str],
    years: list[str] | None,
    tenor: str,
) -> dict[str, dict[str, str]]:
    """product → {tradingday: spread}。"""
    return {
        str(p).upper(): cal_map_for(calendar, p, years, tenor)
        for p in products
    }


@dataclass
class SpreadSchedule:
    """预计算 (product, tradingday) -> spread（固定 tenor）。"""

    tenor: str
    calendar: DominantCalendar
    _map: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_dir(cls, table_dir: str, tenor: str = "dom_sub") -> SpreadSchedule:
        t = normalize_tenor(tenor)
        cal = DominantCalendar(table_dir)
        sched = cls(tenor=t, calendar=cal)
        for prod in cal.products:
            sched._map[prod] = cal_map_for(cal, prod, None, t)
        return sched

    def spread_on(self, product: str, tradingday: str) -> str | None:
        return self._map.get(str(product).upper(), {}).get(str(tradingday))

    def trading_days(self, product: str, years: list[str] | None = None) -> list[str]:
        prod = str(product).upper()
        days = sorted(self._map.get(prod, {}).keys())
        if not years:
            return days
        ys = {str(y) for y in years}
        return [d for d in days if d[:4] in ys]

    def unique_spreads(self, product: str, years: list[str] | None = None) -> list[str]:
        cal_map = {td: self._map.get(str(product).upper(), {}).get(td)
                   for td in self.trading_days(product, years)}
        cal_map = {k: v for k, v in cal_map.items() if v}
        return unique_spreads_from_cal(cal_map)

    def products_in_years(self, years: list[str]) -> list[str]:
        ys = {str(y) for y in years}
        out = []
        for prod, day_map in self._map.items():
            if any(d[:4] in ys for d in day_map):
                out.append(prod)
        return sorted(out)
