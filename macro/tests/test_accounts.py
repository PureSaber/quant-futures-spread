from datetime import date
from decimal import Decimal

import pytest

from quant_macro import bond_replay, fx_portfolio
from quant_macro.account_demo import data
from quant_macro.calendars import Calendar, settlement
from quant_macro.common import canonical
from quant_macro.demo import create_demo
from quant_macro.importers import seal
from quant_macro.study import load_bundle, preflight, run, verify


def test_joint_holidays_weekend_and_coverage():
    c = Calendar(dict(start="2026-01-01", end="2026-01-31", holidays=["2026-01-12"], source="test"))
    assert settlement(date(2026, 1, 9), 2, [c]) == date(2026, 1, 14)
    with pytest.raises(ValueError, match="coverage"):
        settlement(date(2026, 1, 30), 2, [c])
    with pytest.raises(ValueError, match="business"):
        settlement(date(2026, 1, 10), 0, [c])


def test_fx_shared_currency_funding_once_and_pending_nav():
    raw = data()["fx_portfolio"]
    tables = fx_portfolio.replay(raw, {})
    assert {r["currency"] for r in tables["positions"]} == {"USD", "EUR", "GBP"}
    assert len(tables["fills"]) == 4
    # Jan 6 trades are economically exposed but no foreign principal settles until Jan 8.
    eur = [r for r in tables["positions"] if r["currency"] == "EUR"]
    assert Decimal(eur[1]["settled"]) == 0
    assert Decimal(eur[1]["pending"]) == 10000
    assert Decimal(eur[3]["settled"]) == 10000
    assert (
        sum(
            r["reason"] == "funding" and r["currency"] == "USD" and r["datetime"] == "2026-01-06"
            for r in tables["ledger"]
        )
        == 1
    )
    opening_interest = [
        r for r in tables["ledger"] if r["datetime"] == "2026-01-06" and r["currency"] == "USD"
    ][0]
    assert Decimal(opening_interest["amount"]) == Decimal(100000) * Decimal("0.02") / 365
    for nav in tables["nav"]:
        total = sum(
            Decimal(r["value"]) for r in tables["positions"] if r["datetime"] == nav["datetime"]
        )
        assert abs(total - Decimal(nav["nav"])) < Decimal("1e-20")
    for ccy in raw["currencies"]:
        running = Decimal(0)
        for entry in tables["ledger"]:
            if entry["currency"] == ccy:
                running += Decimal(entry["amount"])
                assert running == Decimal(entry["balance"])
    assert tables["nav"][-1]["pending_trades"] == 0


def test_fx_cross_and_zero_cost_capital_conservation():
    raw = data()["fx_portfolio"]
    raw["pairs"].append(
        dict(instrument_id="EUR/GBP", base="EUR", quote="GBP", notional_base=2000, settlement_lag=2)
    )
    for row in raw["observations"]:
        row["rates"] = dict(USD=0, EUR=0, GBP=0)
        row["quotes"] = {
            "EUR/USD": dict(bid=1.2, ask=1.2),
            "GBP/USD": dict(bid=1.5, ask=1.5),
            "EUR/GBP": dict(bid=0.8, ask=0.8),
        }
    t = fx_portfolio.replay(raw, dict(strategy="long", fee_bps=0, borrowing_spread=0))
    assert all(Decimal(r["nav"]) == 100000 for r in t["nav"])
    raw["observations"][2]["quotes"]["EUR/GBP"] = dict(bid=0.9, ask=0.9)
    with pytest.raises(ValueError, match="cross rate"):
        fx_portfolio.replay(raw, {})


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda r: r["observations"][1].update(rates_available_at="2030-01-01T00:00:00Z"),
            "future",
        ),
        (lambda r: r["observations"][1]["quotes"].pop("EUR/USD"), "missing"),
        (lambda r: r["observations"].pop(), "unsettled"),
    ],
)
def test_fx_input_rejections(mutation, match):
    raw = data()["fx_portfolio"]
    if match == "unsettled":
        raw["observations"] = raw["observations"][:7]
    mutation(raw)
    with pytest.raises(ValueError, match=match):
        fx_portfolio.replay(raw, {})


@pytest.mark.parametrize("strategy", ["allocation", "etf_relative_value"])
def test_bond_ledger_coupons_dividends_and_final_nav(strategy):
    tables = bond_replay.replay(
        data()["bond_replay"],
        dict(strategy=strategy, weights={"DEMO:CORP": 0.4, "DEMO:BOND-ETF": 0.2}),
    )
    running = Decimal(0)
    for entry in tables["ledger"]:
        running += Decimal(entry["amount"])
        assert abs(running - Decimal(entry["balance"])) < Decimal("1e-20")
    assert abs(running - Decimal(tables["nav"][-1]["nav"])) < Decimal("1e-20")
    assert any(Decimal(r["amount"]) for r in tables["cashflows"] if r["kind"] == "coupon_principal")
    assert any(
        Decimal(r["amount"]) for r in tables["cashflows"] if r["kind"] == "dividend_entitlement"
    )
    assert all(Decimal(r["settled"]) == 0 for r in tables["positions"][-3:])
    for fill in tables["fills"]:
        assert fill["signal_at"] < fill["datetime"]
        assert Decimal(fill["cash"]) != 0


def test_bond_redemption_clears_position_no_duplicate_principal():
    raw = data()["bond_replay"]
    tables = bond_replay.replay(
        raw, dict(weights={"DEMO:GOV": 0.5}, reinvest=False, close_on="2026-01-15")
    )
    assert len(tables["fills"]) == 1  # redeemed, never sold after maturity
    bought = Decimal(tables["fills"][0]["units"])
    redemption = [
        r
        for r in tables["cashflows"]
        if r["date"] == "2026-01-14" and r["instrument_id"] == "DEMO:GOV"
    ]
    assert len(redemption) == 1
    assert Decimal(redemption[0]["amount"]) == bought * Decimal("1.02")


def test_bond_settlement_date_accrual_and_coupon_entitlement():
    raw = data()["bond_replay"]
    # Jan 6 trade settles Jan 8: buyer does not get the Jan 8 coupon and pays zero accrued.
    raw["instruments"][0]["settlement_lag"] = 2
    t = bond_replay.replay(raw, dict(reinvest=False, weights={"DEMO:CORP": 0.5}))
    assert t["fills"][0]["settlement"] == "2026-01-08"
    assert Decimal(t["fills"][0]["accrued"]) == 0
    coupon = next(r for r in t["cashflows"] if r["instrument_id"] == "DEMO:CORP")
    assert Decimal(coupon["amount"]) == 0


def test_bond_missing_fill_stale_mark_future_borrow_and_unsettled_reject():
    raw = data()["bond_replay"]
    raw["observations"][1]["quotes"].pop("DEMO:CORP")
    with pytest.raises(ValueError, match="fresh quote"):
        bond_replay.replay(raw, {})
    raw = data()["bond_replay"]
    raw["observations"][1]["borrow"].pop("DEMO:BOND-ETF")
    with pytest.raises(ValueError, match="short not located"):
        bond_replay.replay(raw, dict(strategy="etf_relative_value"))
    raw = data()["bond_replay"]
    raw["observations"][1]["borrow"]["DEMO:CORP"]["available_at"] = "2030-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="future borrow"):
        bond_replay.replay(raw, {})
    with pytest.raises(ValueError, match="unsettled"):
        bond_replay.replay(data()["bond_replay"], dict(close_on="2026-01-15"))


@pytest.mark.parametrize(
    "config",
    [
        dict(line="fx_portfolio"),
        dict(line="bond_replay"),
        dict(line="bond_replay", strategy="etf_relative_value"),
    ],
)
def test_account_bundle_preflight_reproduce_tamper_and_seal(tmp_path, config):
    create_demo(tmp_path / "bundle")
    check = preflight(tmp_path / "bundle", config)
    assert check["read_only"] and check["software_preflight"] == "pass"
    run(tmp_path / "bundle", config, tmp_path / "result")
    assert verify(tmp_path / "result")["reproduced"]
    manifest, raw = load_bundle(tmp_path / "bundle")
    meta = {k: v for k, v in manifest.items() if k not in {"schema_version", "data_sha256"}}
    (tmp_path / "meta.json").write_bytes(canonical(meta))
    (tmp_path / "config.json").write_bytes(canonical(config))
    seal(
        tmp_path / "bundle/data.json",
        tmp_path / "meta.json",
        tmp_path / "config.json",
        tmp_path / "sealed",
    )
    assert load_bundle(tmp_path / "sealed")[1] == raw
    manifest["quote_use"] = "reference_only"
    (tmp_path / "bundle/manifest.json").write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="bid/ask"):
        preflight(tmp_path / "bundle", config)
    (tmp_path / "result/nav.csv").write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        verify(tmp_path / "result")
