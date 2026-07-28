"""
performance/metrics.py — 日度加性收益绩效指标。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 244


def summarize(daily: pd.Series, capital: float = 1_000_000.0) -> dict:
    """daily：按交易日的日收益率序列（可含 NaN，按 0 处理）。

    capital 仅作接口兼容；daily 应已在 accounting 侧除以资金。
    波动率/夏普按序列内全部交易日计；
    胜率与 active_days 只看有盈亏的日（r != 0），仅作参考。
    调用方不应传入跨实例对齐后的 NaN→0 padding 序列。
    """
    _ = capital
    r = daily.fillna(0.0).astype(float)
    cum = r.cumsum()
    n = len(r)
    active = r[r != 0]
    n_active = len(active)
    total_ret = float(cum.iloc[-1]) if n else 0.0
    ann_ret = total_ret / n * TRADING_DAYS if n else 0.0
    std = float(r.std()) if n > 1 else 0.0
    sharpe = (float(r.mean()) / std * np.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    # 加性曲线回撤（绝对收益率口径）
    peak = cum.cummax()
    dd = cum - peak
    max_dd = float(dd.min()) if n else 0.0
    if max_dd < 0:
        calmar = round(ann_ret / abs(max_dd), 4)
    else:
        # 无回撤：Calmar 数学上未定义，返回 None（而非误导性的 0）
        calmar = None
    win = float((active > 0).sum() / n_active) if n_active else 0.0
    return {
        "days": n,
        "active_days": n_active,
        "total_return": round(total_ret, 6),
        "annual_return": round(ann_ret, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 6),
        "calmar": calmar,
        "win_rate": round(win, 4),
    }


__all__ = ["summarize", "TRADING_DAYS"]
