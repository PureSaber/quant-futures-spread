import json
import math
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from quant_data_kit.derivatives import write_bundle
from quant_data_kit.derivatives.demo import write_demo
from quant_execution.derivative_replay import verify_artifacts

from qfs_global.basis import analyze_basis
from qfs_global.coverage import coverage_report, merge_bundles
from qfs_global.portfolio import portfolio_selector, run_portfolio
from qfs_global.research import main
from qfs_global.universe import universe


def write(path, contracts, quotes, evidence="synthetic", modes=None):
    return write_bundle(
        path,
        contracts,
        quotes,
        provider="test",
        evidence_kind=evidence,
        rights_note="test generated fixture",
        limits="fixture",
        source={"allowed_modes": modes or ["analysis", "replay"]},
    )


@pytest.fixture
def bundle(tmp_path):
    return write_demo(tmp_path / "bundle", "future")


def requirement(bundle):
    return {
        "venue": "SYNTHETIC",
        "product": "DEMO",
        "expected_sessions": sorted({q.session for q in bundle.quotes}),
        "calendar_source": "synthetic fixture calendar",
        "min_sessions": 10,
        "min_contracts": 2,
        "min_roll_overlaps": 1,
        "min_overlap_sessions": 3,
    }


def portfolio():
    return {
        "account": {},
        "legs": [
            {
                "product": "DEMO",
                "direction": 1,
                "contracts": 1,
                "strategy": "nearby",
                "roll_rule": "expiry",
                "roll_days": 3,
            }
        ],
        "delivery_deadlines": {},
    }


def basis_observation(bundle):
    q = bundle.quotes[0]
    return {
        "instrument_id": q.instrument_id,
        "at": q.at.isoformat(),
        "available_at": q.available_at.isoformat(),
        "spot_id": "INDEX",
        "spot_currency": "USD",
        "spot_at": q.at.isoformat(),
        "spot_available_at": q.available_at.isoformat(),
        "spot": 100,
        "funding_rate": 0.05,
        "dividend_yield": 0.02,
        "carry_known_at": q.at.isoformat(),
        "max_skew_seconds": 60,
        "source": "synthetic spot and carry",
    }


def test_universe_includes_domestic_global_and_commodities():
    rows = universe()["products"]
    assert {"IF", "IH", "IC", "IM", "ES", "NIFTY", "CL", "NG", "GC", "ZC"} <= {
        r["product"] for r in rows
    }
    assert len(rows) == len({r["id"] for r in rows}) == 34
    assert all(
        r["research_enabled"] and not r["live_enabled"] and r["history_status"] == "not_assessed"
        for r in rows
    )


def test_coverage_never_counts_fixture_as_real_history(bundle):
    result = coverage_report([bundle], [requirement(bundle)])["products"][0]
    assert result["contracts"] == 2 and result["roll_overlaps"] == 1
    assert result["status"] == "gap"
    assert result["coverage_gaps"] == ["no_exclusively_real_retrospective_evidence"]
    req = requirement(bundle) | {"product": "ABSENT"}
    missing = coverage_report([bundle], [req])["products"][0]
    assert missing["sessions"] == 0 and missing["missing_sessions"]


def test_coverage_real_label_structural_not_investable(bundle, tmp_path):
    # Retrospective label is only exercised here to test validation branches.
    data = write(
        tmp_path / "retrospective-test", bundle.contracts, bundle.quotes, evidence="retrospective"
    )
    assert (
        coverage_report([data], [requirement(bundle)])["products"][0]["status"]
        == "structural_coverage_pass"
    )
    quotes = [
        replace(q, settlement=None, open=-2, high=1, low=-3, close=-1)
        for q in bundle.quotes
        if q.session != bundle.quotes[0].session
    ]
    data = write(
        tmp_path / "negative",
        [
            replace(c, initial_margin=None, maintenance_margin=None, settlement="physical")
            for c in bundle.contracts
        ],
        quotes,
        evidence="retrospective",
        modes=["analysis"],
    )
    row = coverage_report([data], [requirement(bundle)])["products"][0]
    assert row["missing_sessions"] == [bundle.quotes[0].session]
    assert row["nonpositive_rows"] == len(quotes)
    assert set(row["replay_checks"]) == {
        "missing_margin",
        "missing_settlement",
        "nonpositive_price_unsupported_by_account",
        "source_disallows_replay",
        "delivery_deadlines_must_be_checked_before_replay",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(min_sessions=1000),
        lambda r: r.update(min_contracts=10),
        lambda r: r.update(min_roll_overlaps=5),
    ],
)
def test_coverage_thresholds_expose_gaps(bundle, mutation):
    req = requirement(bundle)
    mutation(req)
    assert len(coverage_report([bundle], [req])["products"][0]["coverage_gaps"]) > 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(extra=1),
        lambda r: r.update(expected_sessions=[]),
        lambda r: r.update(min_sessions=True),
        lambda r: r.update(calendar_source=""),
        lambda r: r.update(expected_sessions=["bad-date"]),
    ],
)
def test_invalid_coverage_requirements(bundle, mutation):
    req = requirement(bundle)
    mutation(req)
    with pytest.raises(ValueError):
        coverage_report([bundle], [req])


def test_history_merge_deduplicates_and_preserves_restrictions(bundle, tmp_path):
    merged = merge_bundles([bundle.root, bundle.root], tmp_path / "merged")
    assert merged.quotes == tuple(sorted(bundle.quotes, key=lambda q: (q.at, q.instrument_id)))
    restricted = write(tmp_path / "restricted", bundle.contracts, bundle.quotes, modes=["analysis"])
    combined = merge_bundles([bundle.root, restricted.root], tmp_path / "combined")
    assert combined.allowed_modes == ["analysis"]
    assert len(combined.manifest["source"]["chunks"]) == 2
    changed = write(
        tmp_path / "changed",
        bundle.contracts,
        [replace(q, volume=q.volume + 1) for q in bundle.quotes],
    )
    with pytest.raises(ValueError, match="overlapping"):
        merge_bundles([bundle.root, changed.root], tmp_path / "bad")
    changed_rules = write(
        tmp_path / "rules",
        [replace(c, tick=Decimal("0.02")) for c in bundle.contracts],
        bundle.quotes,
    )
    with pytest.raises(ValueError, match="rules"):
        merge_bundles([bundle.root, changed_rules.root], tmp_path / "bad-rules")
    real = write(tmp_path / "real", bundle.contracts, bundle.quotes, evidence="retrospective")
    with pytest.raises(ValueError, match="evidence"):
        merge_bundles([bundle.root, real.root], tmp_path / "mixed")
    with pytest.raises(ValueError):
        merge_bundles([], tmp_path / "empty")


def test_coverage_conflicts_and_options_fail(bundle, tmp_path):
    changed = write(
        tmp_path / "changed",
        bundle.contracts,
        [replace(q, volume=q.volume + 1) for q in bundle.quotes],
    )
    with pytest.raises(ValueError, match="overlap"):
        coverage_report([bundle, changed], [requirement(bundle)])
    changed = write(
        tmp_path / "rules",
        [replace(c, tick=Decimal("0.02")) for c in bundle.contracts],
        bundle.quotes,
    )
    with pytest.raises(ValueError, match="definitions"):
        coverage_report([bundle, changed], [requirement(bundle)])
    with pytest.raises(ValueError):
        coverage_report([bundle], [])
    with pytest.raises(ValueError, match="duplicate"):
        coverage_report([bundle], [requirement(bundle), requirement(bundle)])
    options = write_demo(tmp_path / "options", "option")
    with pytest.raises(ValueError):
        coverage_report([options], [requirement(bundle)])
    with pytest.raises(ValueError):
        merge_bundles([options.root], tmp_path / "merged-options")


@pytest.fixture
def index_bundle(bundle, tmp_path):
    return write(
        tmp_path / "index",
        [replace(c, underlying="INDEX") for c in bundle.contracts],
        bundle.quotes,
    )


def test_basis_formula_and_no_future_carry(index_bundle):
    raw = basis_observation(index_bundle)
    row = analyze_basis(index_bundle, [raw])[0]
    q, c = index_bundle.quotes[0], index_bundle.contracts[0]
    years = (c.expiry - q.at).total_seconds() / (365 * 86400)
    assert row["fair_value"] == pytest.approx(100 * math.exp(0.03 * years))
    assert row["basis_points"] == pytest.approx(0.2)
    assert row["annualized_simple_basis"] == pytest.approx(0.002 / years)
    raw["carry_known_at"] = (q.available_at + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="unavailable"):
        analyze_basis(index_bundle, [raw])


@pytest.mark.parametrize(
    "field,value",
    [
        ("spot_currency", "EUR"),
        ("spot_id", "OTHER"),
        ("spot", 0),
        ("funding_rate", 2),
        ("max_skew_seconds", 4000),
        ("source", ""),
        ("spot_at", "2024-01-01T00:00:00Z"),
        ("available_at", "2025-01-02T21:00:00Z"),
        ("at", "2024-01-02T21:00:00Z"),
    ],
)
def test_basis_rejects_bad_join_and_asynchrony(index_bundle, field, value):
    raw = basis_observation(index_bundle)
    raw[field] = value
    with pytest.raises(ValueError):
        analyze_basis(index_bundle, [raw])


def test_basis_missing_and_duplicate(index_bundle):
    raw = basis_observation(index_bundle)
    for rows in ([], [raw, raw], [{"spot": 1}]):
        with pytest.raises(ValueError):
            analyze_basis(index_bundle, rows)


def test_cross_product_portfolio_uses_shared_ledger_and_closes(bundle, tmp_path):
    second = [
        replace(
            c,
            instrument_id=c.instrument_id.replace("DEMO", "SECOND"),
            symbol=c.symbol.replace("DEMO", "SECOND"),
            product="SECOND",
            venue="OTHER_VENUE",
        )
        for c in bundle.contracts
    ]
    quotes = list(bundle.quotes) + [
        replace(q, instrument_id=q.instrument_id.replace("DEMO", "SECOND")) for q in bundle.quotes
    ]
    basket = write(
        tmp_path / "basket",
        [*bundle.contracts, *second],
        sorted(quotes, key=lambda q: (q.at, q.instrument_id)),
    )
    config = portfolio()
    config["legs"].append(config["legs"][0] | {"product": "SECOND", "direction": -1})
    result = run_portfolio(basket.root, config, tmp_path / "result")
    assert result["final_account"]["nav"] != "100000"  # fees/slippage survive offsetting exposures
    assert all(Decimal(n) == 0 for n in result["final_account"]["positions"].values())
    assert any(any(Decimal(q) < 0 for q in d["targets"].values()) for d in result["decisions"])
    assert verify_artifacts(tmp_path / "result")["status"] == "passed"


def test_physical_delivery_requires_deadlines_and_avoids_notice(bundle, tmp_path):
    physical = write(
        tmp_path / "physical",
        [replace(c, settlement="physical") for c in bundle.contracts],
        bundle.quotes,
    )
    config = portfolio()
    with pytest.raises(ValueError, match="deadlines"):
        portfolio_selector(physical, config)
    for c in physical.contracts:
        config["delivery_deadlines"][c.instrument_id] = {
            "at": (c.last_trade_at - timedelta(days=7)).isoformat(),
            "known_at": c.known_at.isoformat(),
            "source": "synthetic first notice",
        }
    result = run_portfolio(physical.root, config, tmp_path / "delivery-result")
    assert result["final_account"]
    # A sparse calendar can jump over the deadline; preflight must reject.
    quotes = [
        q
        for q in physical.quotes
        if q.session not in {"2025-01-13", "2025-01-14", "2025-01-15", "2025-01-16"}
    ]
    gapped = write(tmp_path / "gapped", physical.contracts, quotes)
    with pytest.raises(ValueError, match="crosses delivery"):
        portfolio_selector(gapped, config)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(unknown=1),
        lambda c: c.update(legs=[]),
        lambda c: c["account"].update(mode="analysis"),
        lambda c: c["legs"][0].update(direction=0),
        lambda c: c["legs"][0].update(unknown=1),
        lambda c: c["legs"][0].update(product="MISSING"),
        lambda c: c["legs"].append(c["legs"][0]),
    ],
)
def test_portfolio_rejects_invalid_configs(bundle, mutation):
    config = portfolio()
    mutation(config)
    with pytest.raises(ValueError):
        portfolio_selector(bundle, config)


def test_portfolio_rejects_currency_settlement_and_deadline_errors(bundle, tmp_path):
    mixed = write(
        tmp_path / "mixed",
        [bundle.contracts[0], replace(bundle.contracts[1], currency="EUR")],
        bundle.quotes,
    )
    with pytest.raises(ValueError, match="currency"):
        portfolio_selector(mixed, portfolio())
    missing = write(
        tmp_path / "missing", bundle.contracts, [replace(q, settlement=None) for q in bundle.quotes]
    )
    with pytest.raises(ValueError, match="settlement"):
        portfolio_selector(missing, portfolio())
    for info in (
        {"at": "2025-01-01"},
        {"at": "2025-02-01T00:00:00Z", "known_at": "2024-01-01T00:00:00Z", "source": "test"},
    ):
        config = portfolio()
        config["delivery_deadlines"] = {bundle.contracts[0].instrument_id: info}
        with pytest.raises(ValueError):
            portfolio_selector(bundle, config)


def test_daily_portfolio_rejects_duplicate_and_asynchronous_bars(bundle, tmp_path):
    duplicate = replace(
        bundle.quotes[0],
        at=bundle.quotes[0].at + timedelta(seconds=2),
        available_at=bundle.quotes[0].available_at + timedelta(seconds=2),
    )
    changed = write(
        tmp_path / "intraday",
        bundle.contracts,
        sorted([*bundle.quotes, duplicate], key=lambda q: (q.at, q.instrument_id)),
    )
    with pytest.raises(ValueError, match="one bar"):
        portfolio_selector(changed, portfolio())
    quotes = [
        replace(
            q, at=q.at + timedelta(seconds=2), available_at=q.available_at + timedelta(seconds=2)
        )
        if q == bundle.quotes[0]
        else q
        for q in bundle.quotes
    ]
    changed = write(
        tmp_path / "async", bundle.contracts, sorted(quotes, key=lambda q: (q.at, q.instrument_id))
    )
    with pytest.raises(ValueError, match="synchronized"):
        portfolio_selector(changed, portfolio())


def test_free_chunk_lifecycle_reconciliation_is_explicit(bundle, tmp_path):
    def free(path, contracts, quotes):
        return write_bundle(
            path,
            contracts,
            quotes,
            provider="nse-fo",
            evidence_kind="retrospective",
            rights_note="synthetic test only",
            limits="first observed listing not PIT",
            source={"schema": "qdk.free-derivatives/v1", "allowed_modes": ["analysis"]},
        )

    first = free(tmp_path / "first", bundle.contracts, bundle.quotes)
    later_contracts = [
        replace(
            c, known_at=c.known_at + timedelta(days=1), listed_at=c.listed_at + timedelta(days=1)
        )
        for c in bundle.contracts
    ]
    later = free(tmp_path / "later", later_contracts, bundle.quotes)
    with pytest.raises(ValueError, match="rules"):
        merge_bundles([first.root, later.root], tmp_path / "strict")
    merged = merge_bundles(
        [later.root, first.root], tmp_path / "reconciled", reconcile_first_observed=True
    )
    assert merged.contracts[0].known_at == bundle.contracts[0].known_at
    assert merged.allowed_modes == ["analysis"]
    assert "NOT actual" in merged.manifest["source"]["lifecycle_reconciliation"]


def test_cli_all_workflows(bundle, index_bundle, tmp_path, capsys):
    assert main(["universe"]) == 0
    assert len(json.loads(capsys.readouterr().out)["products"]) == 34
    req = tmp_path / "req.json"
    req.write_text(json.dumps([requirement(bundle)]))
    audit = [
        "coverage",
        "--bundle",
        str(bundle.root),
        "--requirements",
        str(req),
        "--output",
        str(tmp_path / "coverage.json"),
    ]
    assert main(audit) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(audit)
    assert (
        main(
            [
                "merge-history",
                "--bundle",
                str(bundle.root),
                "--output",
                str(tmp_path / "merged-cli"),
            ]
        )
        == 0
    )
    capsys.readouterr()
    observations = tmp_path / "obs.json"
    observations.write_text(json.dumps([basis_observation(index_bundle)]))
    assert (
        main(
            [
                "basis",
                "--bundle",
                str(index_bundle.root),
                "--observations",
                str(observations),
                "--output",
                str(tmp_path / "basis-report"),
            ]
        )
        == 0
    )
    capsys.readouterr()
    config = tmp_path / "portfolio.json"
    config.write_text(json.dumps(portfolio()))
    assert (
        main(
            [
                "portfolio",
                "--bundle",
                str(bundle.root),
                "--config",
                str(config),
                "--output",
                str(tmp_path / "portfolio-report"),
            ]
        )
        == 0
    )
    capsys.readouterr()
