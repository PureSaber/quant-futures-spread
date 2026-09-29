import sys

import pytest

import run_backtest


def test_legacy_cli_requires_explicit_research_opt_in(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["qfs-backtest", "--config", "unused.yaml"])
    with pytest.raises(SystemExit) as exc:
        run_backtest.main()
    assert exc.value.code == 2
    assert "--research-only" in capsys.readouterr().err
