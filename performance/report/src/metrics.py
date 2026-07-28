"""
核心绩效指标计算函数库

所有函数接受标准化的 pandas Series / DataFrame，
返回标量或字典，供各报告模块调用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


# ── 基础统计 ────────────────────────────────────────────────

def cumulative_return(net_value: pd.Series) -> float:
    """累计收益率：(最终净值 - 1)。净值口径为加法 1 + cumsum(日收益)，不复利。"""
    if net_value.empty:
        return np.nan
    return float(net_value.iloc[-1] - 1)


def net_value_series(daily_returns: pd.Series) -> pd.Series:
    """由日收益序列构造净值曲线（不复利 / 加法口径）：1 + cumsum(日收益)。

    与固定名义仓位口径一致：每日盈亏按固定本金折算为简单收益后线性累加，
    而非 cumprod 复利累乘。
    """
    return 1.0 + daily_returns.cumsum()


def gross_pnl_yuan_from_daily(daily: pd.DataFrame) -> float:
    """毛盈亏（元）= 净盈亏 + 手续费；基于日度盯市，含跨日持仓浮盈。"""
    return float(daily["daily_pnl"].sum() + daily["daily_commission"].sum())


def annualized_return(net_value: pd.Series, trading_days: int, annual: int = 252) -> float:
    """
    年化收益率（不复利 / 算术口径）：累计简单收益 × (annual / trading_days)。

    仓位为固定名义本金（NotionalSizer，资金基数恒定、利润不再投入），因此收益
    按固定本金线性年化，而非几何复利累乘。等价于 mean(日收益) × annual。
    trading_days: 实际交易日数量
    """
    if net_value.empty or trading_days <= 0:
        return np.nan
    cum_ret = float(net_value.iloc[-1]) - 1.0
    return float(cum_ret * (annual / trading_days))


def annualized_volatility(daily_returns: pd.Series, annual: int = 252) -> float:
    """年化波动率（剔除空仓日）。

    只用**有盈亏的交易日**（日收益≠0）计算日波动，再按"实际活跃交易日频率"年化：
    ``×√(活跃日数 / 年数)``，其中 年数 = 总日历交易日 / annual。

    这样波动率不会被大量空仓零收益日稀释，且对补零天数（如某品种长期挂 0）保持不变。
    与算术年化收益组合后，夏普 ≡ 以活跃日为采样周期的标准夏普。
    """
    if daily_returns.empty:
        return np.nan
    total_days = len(daily_returns)
    active = daily_returns[daily_returns != 0]
    n_active = len(active)
    if n_active < 2 or total_days <= 0:
        return np.nan
    std = active.std()
    # 使用 epsilon 而非 == 0，防止浮点精度问题（如 std=2.3e-19）
    if abs(std) < 1e-10:
        return np.nan
    active_per_year = n_active * annual / total_days
    return float(std * np.sqrt(active_per_year))


def sharpe_ratio(
    daily_returns: pd.Series,
    trading_days: int,
    risk_free_rate: float = 0.02,
    annual: int = 252,
) -> float:
    """夏普比率：(年化收益 - 无风险利率) / 年化波动率（算术口径，不复利）"""
    net_value = net_value_series(daily_returns)
    ann_ret = annualized_return(net_value, trading_days, annual)
    ann_vol = annualized_volatility(daily_returns, annual)
    # 用 abs < epsilon 而非 == 0，防止浮点精度问题
    if ann_vol is None or np.isnan(ann_vol) or abs(ann_vol) < 1e-10:
        return np.nan
    return float((ann_ret - risk_free_rate) / ann_vol)


def sortino_ratio(
    daily_returns: pd.Series,
    trading_days: int,
    risk_free_rate: float = 0.02,
    annual: int = 252,
) -> float:
    """
    索提诺比率：年化超额收益 / 年化下行波动率。

    下行波动率（目标收益=0，剔除空仓日）：在**有盈亏的交易日**上取 min(日收益,0)
    的平方均值再开方，按"活跃交易日频率"年化（×√(活跃日/年)），与年化波动率口径一致。
    注意不要用 "仅负收益日的样本标准差"——那会去掉负收益的均值、且分母只数负日，
    系统性高估索提诺。
    """
    if daily_returns.empty:
        return np.nan
    net_value = net_value_series(daily_returns)
    ann_ret = annualized_return(net_value, trading_days, annual)
    total_days = len(daily_returns)
    active = daily_returns[daily_returns != 0].to_numpy(dtype=float)
    n_active = active.size
    if n_active < 1 or total_days <= 0:
        return np.nan
    downside = np.minimum(active, 0.0)
    if not (downside < 0).any():
        return np.nan
    active_per_year = n_active * annual / total_days
    downside_dev = float(np.sqrt((downside ** 2).mean()) * np.sqrt(active_per_year))
    if downside_dev < 1e-12:
        return np.nan
    return float((ann_ret - risk_free_rate) / downside_dev)


def max_drawdown(net_value: pd.Series) -> float:
    """最大回撤：max(1 - 净值/历史最高净值)"""
    if net_value.empty:
        return np.nan
    rolling_max = net_value.cummax()
    drawdowns = 1 - net_value / rolling_max
    return float(drawdowns.max())


def max_drawdown_duration(net_value: pd.Series) -> int:
    """
    最大回撤持续天数：从回撤起始日到净值恢复新高当日的交易日数（两端均计入）。
    若回测期末尚未恢复，返回从回撤起始日到末尾的天数。
    """
    if net_value.empty:
        return 0
    rolling_max = net_value.cummax()
    is_drawdown = net_value < rolling_max

    max_dur = 0
    cur_dur = 0
    for in_dd in is_drawdown:
        if in_dd:
            cur_dur += 1
        else:
            if cur_dur > 0:
                # +1 以计入恢复新高当日
                max_dur = max(max_dur, cur_dur + 1)
            cur_dur = 0
    # 若结尾仍处于回撤中（尚未恢复）
    if cur_dur > 0:
        max_dur = max(max_dur, cur_dur)
    return max_dur


def calmar_ratio(net_value: pd.Series, trading_days: int, annual: int = 252) -> float:
    """Calmar比率：年化收益率 / 最大回撤"""
    ann_ret = annualized_return(net_value, trading_days, annual)
    mdd = max_drawdown(net_value)
    if mdd == 0 or np.isnan(mdd):
        return np.nan
    return float(ann_ret / mdd)


# ── 交易层面统计 ────────────────────────────────────────────

def win_rate_by_trade(is_win: pd.Series) -> float:
    """按笔胜率：盈利笔数 / 总笔数"""
    if is_win.empty:
        return np.nan
    return float(is_win.sum() / len(is_win))


def win_rate_by_day(daily_returns: pd.Series) -> float:
    """按交易日胜率：盈利日 / 有盈亏的交易日（剔除空仓 / 零收益日）。

    分母不含空仓零收益日，避免稀疏交易的品种胜率被大量挂 0 的日子拉低。
    """
    if daily_returns.empty:
        return np.nan
    active = daily_returns[daily_returns != 0]
    if active.empty:
        return np.nan
    return float((active > 0).sum() / len(active))


def profit_loss_ratio(profit: pd.Series) -> float:
    """
    盈亏比：平均盈利 / |平均亏损|
    profit: 每笔净盈亏序列
    """
    wins = profit[profit > 0]
    losses = profit[profit < 0]
    if wins.empty or losses.empty:
        return np.nan
    return float(wins.mean() / abs(losses.mean()))


def avg_profit_per_trade(profit: pd.Series) -> float:
    """平均每笔收益（净盈亏）"""
    if profit.empty:
        return np.nan
    return float(profit.mean())


# ── 连续统计 ────────────────────────────────────────────────

def max_consecutive_losses(daily_returns: pd.Series) -> int:
    """最长连续亏损交易日"""
    return _max_consecutive(daily_returns < 0)


def _max_consecutive(mask: pd.Series) -> int:
    max_count = 0
    cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        max_count = max(max_count, cur)
    return max_count


# ── 快速版（直接吃 numpy 数组）：用于 weekly/monthly 内 24k+ 次小循环 ──

def _max_consecutive_np(mask: np.ndarray) -> int:
    """numpy 版 max_consecutive；mask 是 bool ndarray。"""
    if mask.size == 0:
        return 0
    max_count = 0
    cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        if cur > max_count:
            max_count = cur
    return max_count


def week_stats_np(daily_ret: np.ndarray) -> dict:
    """``_week_stats`` / ``_month_stats`` 的 numpy 快速版本。

    输入是 ndarray（float64）。原 pandas 路径里几个 Series 调用每次都要
    构造新对象 + dispatch，在 24k+ 小窗口循环里能省掉 60-70% 时间。
    """
    if daily_ret.size == 0:
        return dict(
            ret=0.0, win_rate=np.nan, max_cons_wins=0, max_cons_losses=0,
            max_cons_loss_amt=0.0, max_dd=np.nan,
        )
    # 期间收益（不复利 / 加法口径）：固定本金下简单收益直接累加。
    period_ret = float(daily_ret.sum())
    n = daily_ret.size
    gt0 = daily_ret > 0
    lt0 = daily_ret < 0
    # 胜率剔除空仓零收益日：分母 = 有盈亏的交易日数。
    n_active = int(gt0.sum() + lt0.sum())
    win_rate = float(gt0.sum() / n_active) if n_active > 0 else np.nan
    max_cons_wins   = _max_consecutive_np(gt0)
    max_cons_losses = _max_consecutive_np(lt0)
    # 最大连续亏损幅度：找连续负段，每段对日收益做加法累加，取最负值。
    max_cons_loss_amt = 0.0
    if lt0.any():
        cur_sum = 0.0
        in_loss = False
        worst = 0.0
        for i in range(n):
            if lt0[i]:
                if not in_loss:
                    cur_sum = 0.0
                    in_loss = True
                cur_sum += daily_ret[i]
                if cur_sum < worst:
                    worst = cur_sum
            else:
                in_loss = False
                cur_sum = 0.0
        max_cons_loss_amt = float(worst)
    # max_drawdown（加法净值）：net = 1 + cumsum，dd = 1 - net/cummax。
    net = 1.0 + np.cumsum(daily_ret)
    rolling_max = np.maximum.accumulate(net)
    dd = 1.0 - net / rolling_max
    max_dd = float(dd.max())
    return dict(
        ret=period_ret, win_rate=win_rate,
        max_cons_wins=max_cons_wins, max_cons_losses=max_cons_losses,
        max_cons_loss_amt=max_cons_loss_amt, max_dd=max_dd,
    )


# ── 月份/年份级统计 ──────────────────────────────────────────

def positive_months(monthly_returns: pd.Series) -> int:
    """正收益月份数"""
    return int((monthly_returns > 0).sum())


def max_consecutive_loss_months(monthly_returns: pd.Series) -> int:
    """最长连续亏损月数"""
    return _max_consecutive(monthly_returns < 0)


# ── 汇总入口：给定日收益序列，返回全套指标字典 ──────────────

def calc_all_metrics(
    daily_returns: pd.Series,
    trades_profit: Optional[pd.Series] = None,
    risk_free_rate: float = 0.02,
    annual: int = 252,
) -> dict:
    """
    给定日收益率序列（及可选的每笔净盈亏序列），
    计算并返回所有核心绩效指标字典。
    """
    net_value = net_value_series(daily_returns)
    trading_days = len(daily_returns)

    result = {
        "累计收益率":       cumulative_return(net_value),
        "年化收益率":       annualized_return(net_value, trading_days, annual),
        "年化波动率":       annualized_volatility(daily_returns, annual),
        "夏普比率":         sharpe_ratio(daily_returns, trading_days, risk_free_rate, annual),
        "索提诺比率":       sortino_ratio(daily_returns, trading_days, risk_free_rate, annual),
        "最大回撤":         max_drawdown(net_value),
        "最大回撤持续天数": max_drawdown_duration(net_value),
        "卡玛比率":         calmar_ratio(net_value, trading_days, annual),
        "交易日胜率":       win_rate_by_day(daily_returns),
    }

    if trades_profit is not None and not trades_profit.empty:
        result.update({
            "总交易次数":   len(trades_profit),
            "按笔胜率":     win_rate_by_trade(trades_profit > 0),
            "盈亏比":       profit_loss_ratio(trades_profit),
            "平均每笔收益": avg_profit_per_trade(trades_profit),
        })

    return result
