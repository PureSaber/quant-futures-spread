"""Fictional multi-currency and bond account examples, including cash-flow events."""

from copy import deepcopy


def data():
    calendar = dict(
        start="2026-01-01",
        end="2026-02-28",
        holidays=["2026-01-01"],
        source="synthetic test calendar, not exchange holidays",
    )
    dates = ["2026-01-" + str(d).zfill(2) for d in [5, 6, 7, 8, 9, 12, 13, 14, 15]]
    first = dates[0] + "T17:00:00+00:00"
    portfolio = dict(
        currencies={
            ccy: dict(calendar=deepcopy(calendar), rate_basis=365) for ccy in ["USD", "EUR", "GBP"]
        },
        pairs=[
            dict(instrument_id=ident, base=base, quote="USD", notional_base=10000, settlement_lag=2)
            for ident, base in [("EUR/USD", "EUR"), ("GBP/USD", "GBP")]
        ],
        observations=[],
    )
    bonds = []
    for ident, maturity in [("DEMO:CORP", "2026-07-08"), ("DEMO:GOV", "2026-01-14")]:
        terms = dict(
            instrument_id=ident,
            currency="USD",
            day_count="ACT/ACT-ICMA",
            periods=[
                dict(
                    start="2025-07-08",
                    end="2026-01-08",
                    payment="2026-01-08",
                    coupon=2,
                    principal=0,
                ),
                dict(start="2026-01-08", end=maturity, payment=maturity, coupon=2, principal=100),
            ],
        )
        bonds.append(
            dict(
                instrument_id=ident, kind="bond", available_at=first, settlement_lag=1, terms=terms
            )
        )
    etf = dict(instrument_id="DEMO:BOND-ETF", kind="etf", available_at=first, settlement_lag=1)
    account = dict(
        currency="USD",
        calendar=calendar,
        instruments=[*bonds, etf],
        observations=[],
        distributions=[
            dict(
                instrument_id=etf["instrument_id"],
                ex_date="2026-01-09",
                pay_date="2026-01-13",
                amount=0.5,
                available_at=first,
            )
        ],
        baskets=[
            dict(
                instrument_id=etf["instrument_id"],
                observed_at=first,
                available_at=first,
                faces_per_share={"DEMO:CORP": 100},
                cash_per_share=0,
            )
        ],
    )
    for i, date in enumerate(dates):
        at = date + "T17:00:00+00:00"
        portfolio["observations"].append(
            dict(
                observed_at=at,
                available_at=at,
                rates_available_at=at,
                rates=dict(USD=0.02, EUR=0.04, GBP=0.03),
                quotes={
                    "EUR/USD": dict(bid=1.1 + i * 0.001 - 0.0001, ask=1.1 + i * 0.001 + 0.0001),
                    "GBP/USD": dict(bid=1.3 + i * 0.001 - 0.0001, ask=1.3 + i * 0.001 + 0.0001),
                },
            )
        )
        quotes = {}
        for ident, px in [
            ("DEMO:CORP", 98 + i * 0.02),
            ("DEMO:GOV", 100),
            (etf["instrument_id"], 102 + i * 0.02),
        ]:
            if ident == "DEMO:GOV" and date >= "2026-01-14":
                continue
            quotes[ident] = dict(
                bid=px - 0.05, ask=px + 0.05, observed_at=at, available_at=at, kind="dealer"
            )
        account["observations"].append(
            dict(
                observed_at=at,
                available_at=at,
                rates_available_at=at,
                funding_rate=0.02,
                quotes=quotes,
                borrow={
                    k: dict(available_at=at, max_units=1000000, rate=0.005)
                    for k in ["DEMO:CORP", "DEMO:GOV", etf["instrument_id"]]
                },
            )
        )
    return {"fx_portfolio": portfolio, "bond_replay": account}
