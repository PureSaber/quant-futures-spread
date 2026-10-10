from dataclasses import replace

import pytest
from quant_data_kit.derivatives import write_bundle
from quant_data_kit.derivatives.demo import write_demo

from qfs_global.study import configuration, preflight, run, selector


@pytest.mark.parametrize(
    "strategy,rule", [("nearby", "expiry"), ("nearby", "volume"), ("calendar_spread", "expiry")]
)
def test_complete_research_roll_and_flat_finish(tmp_path, strategy, rule):
    bundle = write_demo(tmp_path / "bundle", "future")
    result = run(bundle.root, {"strategy": strategy, "roll_rule": rule}, tmp_path / "out")
    assert result["evidence_kind"] == "synthetic"
    assert all(float(q) == 0 for q in result["final_account"]["positions"].values())
    assert (tmp_path / "out" / "rolls.csv").is_file()
    assert len(result["decisions"]) > 10
    assert "next observed" in result["roll_basis"]


def test_volume_selection_uses_only_supplied_observation(tmp_path):
    bundle = write_demo(tmp_path / "bundle", "future")
    now = min(q.available_at for q in bundle.quotes)
    quotes = bundle.asof(now)
    select = selector(bundle, configuration({"roll_rule": "volume"}))
    assert list(select(quotes, now)) == ["DEMO:H"]
    assert list(
        select(
            {i: replace(q, volume=999999 if i == "DEMO:M" else 1) for i, q in quotes.items()}, now
        )
    ) == ["DEMO:M"]
    assert select({}, now) == {}


def test_missing_settlement_is_analysis_only(tmp_path):
    bundle = write_demo(tmp_path / "bundle", "future")
    changed = write_bundle(
        tmp_path / "ohlc",
        bundle.contracts,
        [replace(q, settlement=None) for q in bundle.quotes],
        provider="test",
        evidence_kind="synthetic",
        rights_note="test",
        limits="test",
    )
    with pytest.raises(ValueError, match="settlement"):
        preflight(changed, {})
    assert preflight(changed, {"mode": "analysis"})["read_only"]
    result = run(changed.root, {"mode": "analysis"}, tmp_path / "analysis")
    assert result["final_account"] is None
