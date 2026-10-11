"""Explicit scheduled cash flows, continuous zero curves and parallel risk.

All prices and risks are per 100 face. Schedules are supplied, so odd coupons,
payment calendars and ex-coupon conventions are never silently inferred.
"""

import math
from dataclasses import dataclass
from datetime import date

from .common import currency, day, keys, nonempty, number, positive


def year_fraction(start: date, end: date, convention: str):
    if end < start:
        raise ValueError("negative accrual period")
    if convention == "ACT/365F":
        return (end - start).days / 365
    if convention == "ACT/360":
        return (end - start).days / 360
    if convention == "30E/360":
        return (
            (end.year - start.year) * 360
            + (end.month - start.month) * 30
            + min(end.day, 30)
            - min(start.day, 30)
        ) / 360
    raise ValueError("supported day counts: ACT/365F, ACT/360, 30E/360, ACT/ACT-ICMA")


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    payment: date
    coupon: float
    principal: float


@dataclass(frozen=True)
class Bond:
    instrument_id: str
    currency: str
    day_count: str
    periods: tuple[Period, ...]

    @classmethod
    def parse(cls, raw):
        keys(raw, ("instrument_id", "currency", "day_count", "periods"))
        nonempty(raw["instrument_id"], "instrument_id")
        currency(raw["currency"])
        if raw["day_count"] not in {"ACT/365F", "ACT/360", "30E/360", "ACT/ACT-ICMA"}:
            raise ValueError("unsupported bond day count")
        if not isinstance(raw["periods"], list) or not raw["periods"]:
            raise ValueError("explicit coupon periods required")
        periods = []
        for r in raw["periods"]:
            keys(r, ("start", "end", "payment", "coupon", "principal"))
            p = Period(
                day(r["start"]),
                day(r["end"]),
                day(r["payment"]),
                positive(r["coupon"], "coupon", zero=True),
                positive(r["principal"], "principal", zero=True),
            )
            if p.start >= p.end or p.payment != p.end:
                raise ValueError("v1 requires payment=end and a positive accrual period")
            if periods and periods[-1].end != p.start:
                raise ValueError("coupon periods must be contiguous and ordered")
            periods.append(p)
        if any(p.principal for p in periods[:-1]) or periods[-1].principal != 100:
            raise ValueError("v1 supports bullet bonds redeeming 100 at final payment")
        return cls(raw["instrument_id"], raw["currency"], raw["day_count"], tuple(periods))

    def accrued(self, settlement):
        if not self.periods[0].start <= settlement < self.periods[-1].payment:
            raise ValueError("settlement outside bond life")
        for p in self.periods:
            if p.start <= settlement < p.end:
                if self.day_count == "ACT/ACT-ICMA":
                    fraction = (settlement - p.start).days / (p.end - p.start).days
                else:
                    fraction = year_fraction(p.start, settlement, self.day_count) / year_fraction(
                        p.start, p.end, self.day_count
                    )
                return p.coupon * fraction
        raise ValueError("settlement has no accrual period")

    def flows(self, settlement):
        self.accrued(settlement)
        return [(p.payment, p.coupon + p.principal) for p in self.periods if p.payment > settlement]


@dataclass(frozen=True)
class Curve:
    """ACT/365F continuously compounded zeros, linear interpolation; no extrapolation."""

    tenors: tuple[float, ...]
    zeros: tuple[float, ...]

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, list) or len(raw) < 2:
            raise ValueError("curve needs at least two zero-rate nodes")
        tenors, zeros = [], []
        for node in raw:
            keys(node, ("years", "zero_rate"))
            tenors.append(positive(node["years"], "years", zero=True))
            zeros.append(number(node["zero_rate"], "zero_rate"))
        if tenors[0] != 0 or any(b <= a for a, b in zip(tenors, tenors[1:], strict=False)):
            raise ValueError("curve tenors must start at zero and strictly increase")
        return cls(tuple(tenors), tuple(zeros))

    def zero(self, years):
        t = positive(years, "years", zero=True)
        if t > self.tenors[-1]:
            raise ValueError("cash flow exceeds curve coverage; extrapolation disabled")
        for i in range(1, len(self.tenors)):
            if t <= self.tenors[i]:
                weight = (t - self.tenors[i - 1]) / (self.tenors[i] - self.tenors[i - 1])
                return self.zeros[i - 1] * (1 - weight) + self.zeros[i] * weight
        raise ValueError("curve coverage missing")

    def discount(self, years, spread=0):
        try:
            result = math.exp(-(self.zero(years) + number(spread)) * years)
        except OverflowError as exc:
            raise ValueError("discount factor overflows") from exc
        return positive(result, "discount factor")


def price(bond, settlement, curve, spread=0):
    dirty = sum(
        amount * curve.discount((when - settlement).days / 365, spread)
        for when, amount in bond.flows(settlement)
    )
    accrued = bond.accrued(settlement)
    return {"dirty": positive(dirty), "accrued": accrued, "clean": dirty - accrued}


def solve_spread(bond, settlement, curve, clean):
    target = positive(clean, "clean price") + bond.accrued(settlement)
    low, high = -1.0, 5.0
    if (
        not price(bond, settlement, curve, high)["dirty"]
        <= target
        <= price(bond, settlement, curve, low)["dirty"]
    ):
        raise ValueError("price outside supported z-spread bracket [-1,5]")
    for _ in range(100):
        mid = (low + high) / 2
        if price(bond, settlement, curve, mid)["dirty"] > target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def risk(bond, settlement, curve, spread=0):
    p = price(bond, settlement, curve, spread)
    down = price(bond, settlement, curve, spread - 0.0001)["dirty"]
    up = price(bond, settlement, curve, spread + 0.0001)["dirty"]
    return p | {
        "z_spread": spread,
        "dv01_per_100": (down - up) / 2,
        "effective_duration": (down - up) / (2 * p["dirty"] * 0.0001),
        "convexity": (down + up - 2 * p["dirty"]) / (p["dirty"] * 0.0001**2),
    }


def treasury_future_risk(ctd_dv01_per_100, conversion_factor, face_per_contract):
    """Approximate CTD-based parallel DV01, without delivery-option convexity."""
    return (
        positive(ctd_dv01_per_100) * positive(face_per_contract) / 100 / positive(conversion_factor)
    )


def stir_future(quote, rate_change_bp, value_per_price_point, contracts=1):
    """100-minus-rate quote. Positive rate move loses money for a long future."""
    return {
        "implied_rate": (100 - number(quote)) / 100,
        "pnl": -number(rate_change_bp) * 0.01 * positive(value_per_price_point) * number(contracts),
        "dv01_per_contract": positive(value_per_price_point) * 0.01,
    }
