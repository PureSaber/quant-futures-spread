from __future__ import annotations

import pandas as pd

from core.engine.runner import BacktestResult
from core.io import standard_output
from core.io.config_loader import BacktestConfig


def test_legacy_standard_costs_declare_currency_units(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    sentinel = object()

    def capture_standard_run(*args, **kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(standard_output, "write_standard_run", capture_standard_run)
    monkeypatch.setattr(standard_output, "_code_version", lambda _: "test-version")
    config = BacktestConfig(
        run_id="cost-unit",
        strategies=[],
        data_dir="",
        years=[],
        future_list_path="",
        products=[],
        exclude=[],
        use_trade_flag=False,
        output_dir="",
        capital=1_000_000.0,
    )

    result = standard_output.write_futures_standard_run(
        tmp_path,
        config,
        BacktestResult(),
        pd.DataFrame(),
        pd.DataFrame(),
        {},
        "fixture-strategy",
    )

    assert result is sentinel
    assert captured["tags"]["cost_unit"] == "currency"


def test_legacy_returns_export_the_currency_cost_denominator() -> None:
    config = BacktestConfig(
        run_id="cost-denominator",
        strategies=[],
        data_dir="",
        years=[],
        future_list_path="",
        products=[],
        exclude=[],
        use_trade_flag=False,
        output_dir="",
        capital=1_000.0,
    )
    portfolio = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "strategy": ["fixture-strategy", "fixture-strategy"],
            "num_spreads": [2, 1],
            "commission": [10.0, 3.0],
            "daily_pnl_pct": [0.01, -0.02],
        }
    )

    returns = standard_output._standard_returns(portfolio, config)

    assert returns["return_capital"].tolist() == [2_000.0, 1_000.0]
    assert returns["gross_return"].tolist() == [0.015, -0.017]
    assert returns["nav"].tolist() == [1.01, 0.9898]
