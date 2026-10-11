"""Small explicit synthetic data fixture; every displayed contract is fictional."""

from datetime import date, timedelta
from pathlib import Path

from .common import canonical
from .study import digest


def demo_data():
    rows = []
    start = date(2026, 1, 5)
    for i in range(9):
        when = start + timedelta(days=i)
        mid = 1.10 + 0.001 * i
        at = when.isoformat() + "T17:00:00+00:00"
        forwards = []
        for n in (3, 6, 8):
            maturity = start + timedelta(days=n)
            if maturity > when:
                f = mid * (1 + 0.02 * (n - i) / 365) / (1 + 0.04 * (n - i) / 365)
                forwards.append(
                    {"maturity": maturity.isoformat(), "bid": f - 0.0001, "ask": f + 0.0001}
                )
        rows.append(
            {
                "observed_at": at,
                "available_at": at,
                "rates_available_at": at,
                "bid": mid - 0.0001,
                "ask": mid + 0.0001,
                "base_rate": 0.04,
                "quote_rate": 0.02,
                "forwards": forwards,
                "fixing": mid,
            }
        )
    periods = []
    for i, (begin, end) in enumerate(
        [
            ("2025-07-05", "2026-01-05"),
            ("2026-01-05", "2026-07-05"),
            ("2026-07-05", "2027-01-05"),
            ("2027-01-05", "2027-07-05"),
            ("2027-07-05", "2028-01-05"),
        ]
    ):
        periods.append(
            {
                "start": begin,
                "end": end,
                "payment": end,
                "coupon": 2,
                "principal": 100 if i == 4 else 0,
            }
        )
    at = "2026-01-05T16:00:00+00:00"
    government = {
        "instrument_id": "DEMO:GOV",
        "currency": "USD",
        "day_count": "ACT/ACT-ICMA",
        "periods": periods,
    }
    corporate = government | {"instrument_id": "DEMO:CORP"}
    return {
        "fx": {
            "base": "EUR",
            "quote": "USD",
            "base_rate_basis": 365,
            "quote_rate_basis": 365,
            "quotes": rows,
        },
        "fixed_income": {
            "bonds": [
                {"terms": government, "sector": "government", "available_at": at},
                {"terms": corporate, "sector": "corporate", "available_at": at},
            ],
            "curves": [
                {
                    "observed_at": at,
                    "available_at": at,
                    "currency": "USD",
                    "anchor": "2026-01-05",
                    "nodes": [
                        {"years": 0, "zero_rate": 0.03},
                        {"years": 1, "zero_rate": 0.035},
                        {"years": 3, "zero_rate": 0.04},
                    ],
                }
            ],
            "bond_quotes": [
                {
                    "instrument_id": ident,
                    "observed_at": at,
                    "available_at": at,
                    "bid": mid - 0.1,
                    "ask": mid + 0.1,
                    "kind": "dealer",
                }
                for ident, mid in [("DEMO:GOV", 100), ("DEMO:CORP", 97)]
            ],
            "etfs": [
                {
                    "instrument_id": "DEMO:BOND-ETF",
                    "currency": "USD",
                    "observed_at": at,
                    "available_at": at,
                    "shares": 1000,
                    "cash": 1000,
                    "liabilities": 100,
                    "holdings": [
                        {"instrument_id": "DEMO:GOV", "face": 50000},
                        {"instrument_id": "DEMO:CORP", "face": 50000},
                    ],
                }
            ],
            "etf_quotes": [
                {
                    "instrument_id": "DEMO:BOND-ETF",
                    "observed_at": at,
                    "available_at": at,
                    "bid": 99.3,
                    "ask": 99.5,
                    "kind": "dealer",
                }
            ],
            "rate_futures": [
                {
                    "instrument_id": "DEMO:TREASURY-FUT",
                    "kind": "treasury",
                    "currency": "USD",
                    "observed_at": at,
                    "available_at": at,
                    "rule_source": "synthetic fixture, not exchange rules",
                    "ctd_id": "DEMO:GOV",
                    "conversion_factor": 0.9,
                    "face_per_contract": 100000,
                    "price": 111,
                },
                {
                    "instrument_id": "DEMO:STIR",
                    "kind": "stir",
                    "currency": "USD",
                    "observed_at": at,
                    "available_at": at,
                    "rule_source": "synthetic fixture, not exchange rules",
                    "price": 96,
                    "value_per_price_point": 2500,
                },
            ],
        },
    }


def create_demo(output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    data = canonical(demo_data())
    manifest = {
        "schema_version": "quant.macro.bundle/v1",
        "evidence_kind": "synthetic",
        "source": "quant-macro deterministic fictional fixture",
        "license": "repository license; synthetic fixture",
        "retrieved_at": "2026-01-14T00:00:00+00:00",
        "data_sha256": digest(data),
        "quote_use": "observed_bid_ask",
        "limits": "Fictional quotes/rates/bonds; not real market history or rules.",
    }
    (root / "data.json").write_bytes(data)
    (root / "manifest.json").write_bytes(canonical(manifest))
    return {"bundle": str(root.resolve()), "evidence_kind": "synthetic"}
