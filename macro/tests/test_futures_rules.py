from dataclasses import replace
from decimal import Decimal

import pytest

from quant_macro.futures_cli import treasury_decimal


def test_treasury_fractional_quote_conversion():
    assert treasury_decimal("112-165") == Decimal("112.515625")
    assert treasury_decimal("112-16") == Decimal("112.5")
    for bad in ["112-32", "112-161", "112.16", None]:
        with pytest.raises(ValueError):
            treasury_decimal(bad)


@pytest.mark.parametrize("product", ["6E", "ZN", "SR3", "TS", "TF", "T", "TL"])
def test_real_product_units_gate_and_existing_engine_replay(tmp_path, product):
    pytest.importorskip("qfs_global")
    from quant_data_kit.derivatives import load_bundle
    from quant_execution.derivative_replay import verify_artifacts

    from quant_macro.futures_cli import run, validate_rules
    from quant_macro.futures_demo import create

    create(tmp_path / "bundle", product)
    bundle = load_bundle(tmp_path / "bundle")
    config = dict(product=product)
    check = validate_rules(bundle, config)
    assert check["rule_profile"] == product and not check["historical_rules_certified"]
    study = run(tmp_path / "bundle", config, tmp_path / "result")
    assert study["evidence_kind"] == "synthetic"
    assert len(study["decisions"]) > 3
    assert verify_artifacts(tmp_path / "result")["status"] == "passed"
    broken = replace(
        bundle, contracts=tuple(replace(c, multiplier=c.multiplier * 100) for c in bundle.contracts)
    )
    with pytest.raises(ValueError, match="multiplier"):
        validate_rules(broken, config)
    broken = replace(
        bundle,
        quotes=(
            replace(bundle.quotes[0], open=bundle.quotes[0].open + Decimal("0.000001")),
            *bundle.quotes[1:],
        ),
    )
    with pytest.raises(ValueError, match="off-tick"):
        validate_rules(broken, config)
