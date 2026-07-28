"""factor_single 策略单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from strategies.factor_single.factors import compute_factor_value, factor_ic_sign


def _bars(n: int = 80, base: float = 100.0):
    out = []
    for i in range(n):
        c = base + (i % 10) - 5
        out.append({
            "close_price": c,
            "low_price": c - 1,
            "high_price": c + 1,
            "bid_low": c - 2,
            "ask_high": c + 2,
        })
    return out


class _Bar:
    close_price = 105.0
    bid_low = 103.0
    ask_high = 107.0


def test_factor_ic_sign():
    assert factor_ic_sign("breakout_down_60") == 1
    assert factor_ic_sign("mid_dev") == -1


def test_compute_mid_dev():
    bars = _bars()
    v = compute_factor_value("mid_dev", bars, _Bar(), "A2405&A2409", 60)
    assert v is not None


def test_compute_pct_rank():
    bars = _bars()
    v = compute_factor_value("pct_rank", bars, _Bar(), "A2405&A2409", 60)
    assert v is not None
    assert 0 <= v <= 1.5
