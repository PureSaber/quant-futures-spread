"""research 流水线 smoke 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.common.config import load_config
from research.factors.compute import compute_factors
from research.universe.build_manifest import build_manifest


def test_smoke_manifest():
    cfg = load_config(ROOT / "research/config/factor_mining_smoke.yaml")
    m = build_manifest(cfg)
    assert len(m) >= 2
    assert set(m["pair_type"]).issubset({"calendar", "cross"})


def test_compare_ic_summaries():
    from research.eval.ic_analysis import compare_ic_summaries

    mid = pd.DataFrame([
        {"factor": "mid_dev", "horizon": 15, "ic_mean": -0.32, "ic_std": 0.1, "ic_count": 100, "icir": -3.2, "abs_ic_mean": 0.32},
    ])
    realized = pd.DataFrame([
        {"factor": "mid_dev", "horizon": 15, "ic_mean": -0.05, "ic_std": 0.1, "ic_count": 100, "icir": -0.5, "abs_ic_mean": 0.05},
    ])
    cmp = compare_ic_summaries(mid, realized)
    assert "ic_decay" in cmp.columns
    assert cmp.iloc[0]["ic_decay"] == pytest.approx(0.27, rel=1e-3)


def test_compute_ic_panel_label_kinds():
    from research.eval.ic_analysis import compute_ic_panel, summarize_ic

    df = pd.DataFrame({
        "spread_id": ["s1"] * 50,
        "product": ["A"] * 50,
        "sector": ["Agri"] * 50,
        "tradingday": ["2024-01-02"] * 50,
        "datetime": pd.date_range("2024-01-02", periods=50, freq="min"),
        "z_close": np.linspace(-2, 2, 50),
        "fwd_ret_5": np.linspace(-1, 1, 50),
        "fwd_realized_long_5": np.linspace(-2, 0, 50),
    })
    mid = summarize_ic(compute_ic_panel(df, [5], label_kind="mid"))
    realized = summarize_ic(compute_ic_panel(
        df, [5], label_template="fwd_realized_long_{h}", label_kind="realized_long",
    ))
    assert not mid.empty
    assert not realized.empty
    assert mid.iloc[0]["label_kind"] == "mid"
    assert realized.iloc[0]["label_kind"] == "realized_long"


def test_compute_factors_on_sample():
    sample = ROOT / "data/local_sample/dom_sub/MarketData/2020/A/A2103&A2105.csv"
    if not sample.is_file():
        return
    df = pd.read_csv(sample)
    out = compute_factors(df, "A2103&A2105", 2020, lookback=20)
    core = {"z_close", "pct_rank", "mom_5", "eff_spread", "carry_ann", "pair_z"}
    for name in core:
        assert name in out.columns, f"missing {name}"
