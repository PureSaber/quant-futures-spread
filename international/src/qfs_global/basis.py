"""Synchronized index/future basis with explicit carry, identity and availability."""

import math

from quant_data_kit.derivatives.models import decimal, utc


def analyze_basis(bundle, observations):
    bundle.require_mode("analysis")
    if not observations:
        raise ValueError("spot and carry observations required")
    by_key = {}
    for raw in observations:
        required = {
            "instrument_id",
            "at",
            "available_at",
            "spot_id",
            "spot_currency",
            "spot_at",
            "spot_available_at",
            "spot",
            "funding_rate",
            "dividend_yield",
            "carry_known_at",
            "max_skew_seconds",
            "source",
        }
        if not isinstance(raw, dict) or set(raw) != required:
            raise ValueError("explicit synchronized spot/carry schema required")
        c = bundle.contract(raw["instrument_id"])
        at, available = utc(raw["at"]), utc(raw["available_at"])
        spot_at, spot_available = utc(raw["spot_at"]), utc(raw["spot_available_at"])
        if (
            c.kind != "future"
            or not c.underlying
            or c.underlying != raw["spot_id"]
            or c.currency != raw["spot_currency"]
            or c.underlying_kind != "spot"
        ):
            raise ValueError("future/spot identity or currency mismatch")
        skew = decimal(raw["max_skew_seconds"])
        if not 0 <= skew <= 3600 or not raw["source"]:
            raise ValueError("source and max_skew_seconds in [0,3600] required")
        if (
            not at <= available
            or not spot_at <= spot_available <= available
            or utc(raw["carry_known_at"]) > available
            or abs((spot_at - at).total_seconds()) > skew
        ):
            raise ValueError("spot/carry unavailable or observations asynchronous")
        if c.known_at > available or c.expiry <= at:
            raise ValueError("contract unavailable or expired")
        spot = float(decimal(raw["spot"]))
        r, q = float(decimal(raw["funding_rate"])), float(decimal(raw["dividend_yield"]))
        if spot <= 0 or abs(r) > 1 or not 0 <= q <= 1:
            raise ValueError("positive spot and bounded annual carry required")
        key = (c.instrument_id, at)
        if key in by_key:
            raise ValueError("duplicate basis observation")
        by_key[key] = (raw, available, spot, r, q)
    result = []
    quotes = {(q.instrument_id, q.at): q for q in bundle.quotes}
    for key, (raw, available, spot, r, q) in sorted(by_key.items()):
        observed = quotes.get(key)
        if observed is None or observed.available_at > available:
            raise ValueError("matching future observation unavailable")
        c = bundle.contract(key[0])
        years = (c.expiry - key[1]).total_seconds() / (365 * 86400)
        if years > 30:
            raise ValueError("basis maturity exceeds 30 years")
        fair = spot * math.exp((r - q) * years)
        future = float(observed.close)
        if future <= 0:
            raise ValueError("positive index future price required")
        result.append(
            {
                "instrument_id": key[0],
                "at": key[1].isoformat(),
                "available_at": available.isoformat(),
                "spot_id": raw["spot_id"],
                "spot": spot,
                "future": future,
                "basis_points": future - spot,
                "basis_ratio": future / spot - 1,
                "annualized_simple_basis": (future / spot - 1) / years,
                "implied_continuous_carry": math.log(future / spot) / years,
                "fair_value": fair,
                "residual_points": future - fair,
                "currency": c.currency,
                "source": raw["source"],
            }
        )
    bundle.verify_unchanged()
    return result
