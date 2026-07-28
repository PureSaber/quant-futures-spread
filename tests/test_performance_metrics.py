"""tests/test_performance_metrics.py —— 绩效 summarize 口径。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from performance import summarize


def ok(cond, msg):
    assert cond, msg


def test_sharpe_uses_all_in_window_days():
    """Sharpe/std 按序列内全部交易日计，不剔除持平日。"""
    idx = pd.date_range("2021-01-04", periods=10, freq="B")
    active = pd.Series([0.01, -0.005, 0.008, 0.012, 0.006], index=idx[:5])
    padded = active.reindex(idx, fill_value=0.0)
    s_active = summarize(active)
    s_padded = summarize(padded)
    ok(s_active["sharpe"] != s_padded["sharpe"],
       "补 0 日改变全窗口 std → Sharpe 不同（证明用全部日，非 active-only）")
    ref = active.reindex(idx, fill_value=0.0)
    expected = round(ref.mean() / ref.std() * (244 ** 0.5), 4)
    ok(abs(s_padded["sharpe"] - expected) < 1e-9,
       "Sharpe = mean/std × sqrt(244)，分母含全部日")
    ok(s_padded["active_days"] == 5 and s_padded["days"] == 10,
       "active_days 仅计有盈亏日；days 计全部日")


def test_additive_drawdown_and_total():
    r = pd.Series([0.1, 0.05, -0.08, 0.02])
    s = summarize(r)
    ok(abs(s["total_return"] - 0.09) < 1e-9, "total_return = cumsum")
    cum = r.cumsum()
    ok(abs(s["max_drawdown"] - float((cum - cum.cummax()).min())) < 1e-9,
       "max_drawdown 对齐加性曲线")


def test_calmar_no_drawdown():
    r = pd.Series([0.01, 0.02, 0.01])
    s = summarize(r)
    ok(s["max_drawdown"] == 0.0, "单调上行 max_dd=0")
    ok(s["calmar"] is None, "无回撤时 Calmar 未定义，返回 None 而非 0")


def test_calmar_with_drawdown():
    r = pd.Series([0.1, -0.04, 0.02])
    s = summarize(r)
    ok(s["max_drawdown"] < 0, "存在回撤")
    ok(isinstance(s["calmar"], float), "有回撤时 Calmar 为数值")


def test_win_rate_active_only():
    r = pd.Series([0.01, 0.0, 0.0, -0.01, 0.02])
    s = summarize(r)
    ok(abs(s["win_rate"] - round(2 / 3, 4)) < 1e-9, "胜率 = 盈利 active 日 / active 日")


if __name__ == "__main__":
    test_sharpe_uses_all_in_window_days()
    test_additive_drawdown_and_total()
    test_calmar_no_drawdown()
    test_calmar_with_drawdown()
    test_win_rate_active_only()
    print("ALL PERFORMANCE METRICS TESTS PASSED")
