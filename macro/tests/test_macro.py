import copy
import csv
import math
from datetime import date
from decimal import Decimal

import pytest

from quant_macro import bonds, fx
from quant_macro.common import canonical, read_json
from quant_macro.demo import create_demo, demo_data
from quant_macro.fixed_income import (
    Bond,
    Curve,
    price,
    risk,
    solve_spread,
    stir_future,
    treasury_future_risk,
    year_fraction,
)
from quant_macro.study import digest, load_bundle, preflight, run, verify


def zero_bond():
    return Bond.parse(
        {
            "instrument_id": "ZERO",
            "currency": "USD",
            "day_count": "ACT/365F",
            "periods": [
                {
                    "start": "2025-01-01",
                    "end": "2026-01-01",
                    "payment": "2026-01-01",
                    "coupon": 0,
                    "principal": 100,
                }
            ],
        }
    )


def flat_curve(rate=0.05):
    return Curve.parse([{"years": 0, "zero_rate": rate}, {"years": 10, "zero_rate": rate}])


def test_zero_coupon_analytic_price_duration_convexity_and_spread():
    bond, settlement, curve = zero_bond(), date(2025, 1, 1), flat_curve()
    value = price(bond, settlement, curve)
    assert value["dirty"] == pytest.approx(100 * math.exp(-0.05))
    measure = risk(bond, settlement, curve)
    assert measure["effective_duration"] == pytest.approx(1, rel=1e-7)
    assert measure["convexity"] == pytest.approx(1, rel=1e-7)
    assert measure["dv01_per_100"] == pytest.approx(value["dirty"] * 0.0001, rel=1e-7)
    assert solve_spread(bond, settlement, curve, 100 * math.exp(-0.08)) == pytest.approx(0.03)


def test_coupon_boundary_and_accrual_are_not_double_counted():
    bond = Bond.parse(demo_data()["fixed_income"]["bonds"][0]["terms"])
    settlement = date(2026, 1, 5)
    assert bond.accrued(settlement) == 0
    assert len(bond.flows(settlement)) == 4
    assert all(when > settlement for when, _ in bond.flows(settlement))
    assert bond.accrued(date(2026, 4, 5)) == pytest.approx(2 * 90 / 181)
    with pytest.raises(ValueError, match="outside bond life"):
        bond.flows(date(2028, 1, 5))


def test_curve_does_not_extrapolate_and_negative_rates_are_valid():
    curve = Curve.parse([{"years": 0, "zero_rate": -0.01}, {"years": 1, "zero_rate": 0.01}])
    assert curve.zero(0.5) == pytest.approx(0)
    assert flat_curve(-0.01).discount(1) > 1
    with pytest.raises(ValueError, match="coverage"):
        curve.discount(2)


def test_daycounts_and_ctd_stir_units():
    assert year_fraction(date(2024, 2, 29), date(2024, 3, 31), "ACT/360") == 31 / 360
    assert year_fraction(date(2026, 1, 31), date(2026, 2, 28), "30E/360") == 28 / 360
    assert treasury_future_risk(0.08, 0.8, 100000) == 100
    stir = stir_future(95, 25, 2500, 2)
    assert stir == {"implied_rate": 0.05, "pnl": -1250, "dv01_per_contract": 25}


def test_covered_interest_parity_direction_and_bases():
    assert fx.forward_price(1.2, 0.02, 0.05, 1) == pytest.approx(1.2 * 1.05 / 1.02)
    assert fx.forward_price(1.2, 0.02, 0.05, 30 / 360, 30 / 365) == pytest.approx(
        1.2 * (1 + 0.05 * 30 / 365) / (1 + 0.02 * 30 / 360)
    )
    with pytest.raises(ValueError, match="funding factor"):
        fx.forward_price(1, -2, 0, 1)


def test_spot_cash_conservation_and_analytic_cost_pnl():
    data = demo_data()["fx"]
    data["quotes"] = data["quotes"][:3]
    for row in data["quotes"]:
        row.update(bid=1.099, ask=1.101, base_rate=0, quote_rate=0)
    result = fx.replay(data, {"strategy": "long", "fee_bps": 0, "notional_base": 10000})
    # Buy 10k base at 1.101, sell at 1.099: exactly 20 quote currency lost.
    assert Decimal(result["nav"][-1]["nav"]) == Decimal(99980)
    assert Decimal(result["positions"][-1]["base_cash"]) == 0
    balances = {"EUR": Decimal(0), "USD": Decimal(0)}
    for event in result["ledger"]:
        balances[event["currency"]] += Decimal(event["amount"])
        assert balances[event["currency"]] == Decimal(event["balance"])
    assert balances == {"EUR": Decimal(0), "USD": Decimal(99980)}


def test_previous_signal_and_future_mutation_cannot_change_earlier_account():
    data = demo_data()["fx"]
    original = fx.replay(data, {"fee_bps": 0})
    altered = copy.deepcopy(data)
    altered["quotes"][4]["base_rate"] = -0.1
    changed = fx.replay(altered, {"fee_bps": 0})
    assert changed["nav"][:5] == original["nav"][:5]
    reversal = next(r for r in changed["fills"] if Decimal(r["quantity"]) < -10000)
    assert reversal["timestamp"] == data["quotes"][5]["available_at"]
    assert all(row["signal_at"] < row["timestamp"] for row in changed["fills"])


def test_forward_rolls_and_settlement_without_spot_funding():
    data = demo_data()["fx"]
    result = fx.replay(data, {"instrument": "forward", "fee_bps": 0})
    settlements = [r for r in result["rolls"] if r["event"] == "settle"]
    assert len(settlements) == 3
    assert all(Decimal(r["base_cash"]) == 0 for r in result["positions"])
    assert Decimal(result["positions"][-1]["forward_quantity"]) == 0
    first = result["fills"][0]
    fixing = Decimal(str(data["quotes"][3]["fixing"]))
    expected = Decimal(first["quantity"]) * (fixing - Decimal(first["price"]))
    assert Decimal(settlements[0]["pnl"]) == expected


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda d: d["quotes"][3].pop("fixing"), "fixing"),
        (lambda d: d["quotes"][2].update(forwards=[]), "same-maturity"),
        (
            lambda d: d["quotes"][1].update(available_at="2026-01-05T17:00:00+00:00"),
            "availability|available",
        ),
        (lambda d: d["quotes"][1].update(bid=3), "crossed"),
        (lambda d: d["quotes"][1].update(base_rate=float("nan")), "finite"),
    ],
)
def test_forward_input_failures(mutation, match):
    data = demo_data()["fx"]
    mutation(data)
    with pytest.raises(ValueError, match=match):
        fx.replay(data, {"instrument": "forward"})


def test_leverage_limit_is_enforced_before_opening():
    with pytest.raises(ValueError, match="leverage"):
        fx.replay(demo_data()["fx"], {"notional_base": 1000000})


def test_credit_etf_nav_uses_dirty_prices_cash_and_liabilities():
    result = bonds.analyze(demo_data()["fixed_income"], {"line": "credit"})
    assert result["etfs"][0]["basket_nav"] == pytest.approx((50000 + 48500 + 1000 - 100) / 1000)
    assert result["etfs"][0]["premium_discount"] == pytest.approx(0)
    assert result["valuations"][1]["z_spread"] > result["valuations"][0]["z_spread"]
    assert all(r["pnl_per_100"] < 0 for r in result["scenarios"])


def test_unpublished_quote_never_enters_snapshot():
    raw = demo_data()["fixed_income"]
    original = bonds.analyze(raw, {"line": "credit"})
    raw["bond_quotes"].append(
        raw["bond_quotes"][1] | {"bid": 50, "ask": 51, "available_at": "2026-01-06T17:00:00+00:00"}
    )
    assert bonds.analyze(raw, {"line": "credit"}) == original


def test_stale_prices_reject_or_are_explicitly_modeled():
    raw = demo_data()["fixed_income"]
    raw["bond_quotes"][1]["observed_at"] = "2025-12-20T17:00:00+00:00"
    with pytest.raises(ValueError, match="stale"):
        bonds.analyze(raw, {"line": "credit"})
    result = bonds.analyze(raw, {"line": "credit", "stale_policy": "model"})
    assert result["valuations"][1]["valuation_basis"] == "model"
    assert result["valuations"][1]["has_fresh_market_quote"] is False
    assert result["etfs"][0]["basket_basis"] == "contains_model_values"


def test_future_holdings_do_not_replace_published_basket():
    raw = demo_data()["fixed_income"]
    original = bonds.analyze(raw, {"line": "credit"})
    raw["etfs"].append(raw["etfs"][0] | {"shares": 2, "available_at": "2026-01-06T17:00:00+00:00"})
    assert bonds.analyze(raw, {"line": "credit"}) == original


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda d: d["etfs"][0]["holdings"][0].update(instrument_id="MISSING"), "constituent"),
        (lambda d: d["bonds"][1]["terms"].update(currency="EUR"), "currency"),
        (lambda d: d["curves"][0].update(available_at="2026-02-05T17:00:00+00:00"), "curve"),
        (lambda d: d["curves"][0]["nodes"][-1].update(years=1.1), "coverage"),
        (lambda d: d["bonds"][0]["terms"]["periods"][0].update(principal=20), "bullet"),
    ],
)
def test_bond_contract_failures(change, match):
    raw = demo_data()["fixed_income"]
    change(raw)
    with pytest.raises(ValueError, match=match):
        bonds.analyze(raw, {"line": "credit"})


@pytest.mark.parametrize(
    "config",
    [
        {"line": "fx"},
        {"line": "fx", "instrument": "forward"},
        {"line": "rates"},
        {"line": "credit"},
    ],
)
def test_full_run_is_readonly_preflight_immutable_output_and_reproducible(tmp_path, config):
    bundle, output = tmp_path / "bundle", tmp_path / "result"
    create_demo(bundle)
    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    assert preflight(bundle, config)["software_preflight"] == "pass"
    assert not output.exists()
    run(bundle, config, output)
    assert verify(output)["reproduced"]
    assert before == {p.name: p.read_bytes() for p in bundle.iterdir()}
    with pytest.raises(FileExistsError):
        run(bundle, config, output)
    (output / "report.html").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(output)


def test_rehashing_changed_nav_does_not_bypass_replay(tmp_path):
    create_demo(tmp_path / "bundle")
    output = tmp_path / "result"
    run(tmp_path / "bundle", {"line": "fx"}, output)
    nav = output / "nav.csv"
    rows = list(csv.DictReader(nav.read_text().splitlines()))
    rows[-1]["nav"] = "1000000"
    with nav.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    artifact = read_json(output / "artifacts.json")
    artifact["files"]["nav.csv"] = digest(nav.read_bytes())
    (output / "artifacts.json").write_bytes(canonical(artifact))
    with pytest.raises(ValueError, match="deterministic replay"):
        verify(output)


def test_bundle_hash_reference_only_and_path_validation(tmp_path):
    create_demo(tmp_path / "bundle")
    manifest_path = tmp_path / "bundle" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["quote_use"] = "reference_only"
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="reference-only"):
        preflight(tmp_path / "bundle", {"line": "fx"})
    data_path = tmp_path / "bundle" / "data.json"
    data_path.write_bytes(data_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_bundle(tmp_path / "bundle")


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"bid": 1, "bid": 2}')
    with pytest.raises(ValueError, match="duplicate"):
        read_json(path)
