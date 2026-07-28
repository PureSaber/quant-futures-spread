"""
核心绩效概览表

输出：整个回测期的汇总指标，一行数据。
包含：累计收益率、年化收益率、年化波动率、夏普、索提诺、
      最大回撤、最大回撤持续天数、胜率、盈亏比、
      总交易次数、平均每笔收益、Calmar比率、
      以及 6 个手续费相关字段（跳过 手续费/ATR 字段）
"""

from __future__ import annotations

import math
import pandas as pd
from src.metrics import calc_all_metrics, gross_pnl_yuan_from_daily, profit_loss_ratio


def build(
    portfolio_daily: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    rf = config.get("risk_free_rate", 0.02)
    annual = config.get("trading_days_per_year", 252)

    daily_returns = portfolio_daily["daily_pnl_pct"]
    metrics = calc_all_metrics(
        daily_returns=daily_returns,
        trades_profit=trades["profit"],
        risk_free_rate=rf,
        annual=annual,
    )

    # ── 手续费指标 ───────────────────────────────────────────────
    total_commission = float(trades["commission"].sum())
    n_trades = len(trades)

    # 净盈亏（元）= 扣除手续费后的实际盈亏
    net_pnl_yuan = float(portfolio_daily["daily_pnl"].sum())

    # 毛盈亏（元）= 净盈亏 + 手续费；须用日度汇总（含跨日持仓浮盈）
    gross_pnl_yuan = gross_pnl_yuan_from_daily(portfolio_daily)

    # 毛累计收益率（不复利 / 加法口径）
    gross_cum_ret = float(portfolio_daily["gross_daily_pnl_pct"].sum())
    net_cum_ret = metrics["累计收益率"]

    # 毛盈亏比（使用 pnl 字段代替 profit）
    gross_plr = float("nan")
    if "pnl" in trades.columns:
        gross_plr_val = profit_loss_ratio(trades["pnl"])
        gross_plr = gross_plr_val if not math.isnan(gross_plr_val) else float("nan")
    net_plr = metrics.get("盈亏比", float("nan"))

    # 手续费/盈亏比损失
    if not math.isnan(gross_plr) and gross_plr > 0 and not math.isnan(net_plr):
        plr_loss = (gross_plr - net_plr) / gross_plr
    else:
        plr_loss = float("nan")

    avg_commission = total_commission / n_trades if n_trades > 0 else 0.0

    def _pct_or_na(num, denom, loss=False):
        """num/denom as pct string (4 decimal places); 'N/A（亏损）' if loss and denom<=0."""
        if denom <= 0:
            return "N/A（亏损）" if loss else "N/A"
        return f"{num / denom:.4%}"

    # ── 格式化输出 ───────────────────────────────────────────────
    fmt = {
        "累计收益率":       f"{net_cum_ret:.2%}",
        "年化收益率":       f"{metrics['年化收益率']:.2%}",
        "年化波动率":       f"{metrics['年化波动率']:.2%}",
        "夏普比率":         f"{metrics['夏普比率']:.2f}",
        "索提诺比率":       f"{metrics['索提诺比率']:.2f}",
        "最大回撤":         f"{metrics['最大回撤']:.2%}",
        "最大回撤持续天数": f"{metrics['最大回撤持续天数']}天",
        "卡玛比率":         f"{metrics['卡玛比率']:.2f}",
        "交易日胜率":       f"{metrics['交易日胜率']:.2%}",
        "总交易次数":       metrics.get("总交易次数", "N/A"),
        "按笔胜率":         f"{metrics['按笔胜率']:.2%}" if "按笔胜率" in metrics else "N/A",
        "盈亏比":           f"{net_plr:.2f}" if not math.isnan(net_plr) else "N/A",
        "平均每笔收益(元)": f"{metrics['平均每笔收益']:.2f}" if "平均每笔收益" in metrics else "N/A",
        # 手续费字段
        "净盈亏（元）":           f"{net_pnl_yuan:,.0f}",
        "毛盈亏（元）":           f"{gross_pnl_yuan:,.0f}",
        "总手续费（元）":         f"{total_commission:,.0f}",
        "毛累计收益率":           f"{gross_cum_ret:.4%}",
        "净收益vs毛收益差":       f"{gross_cum_ret - net_cum_ret:.4%}",
        "手续费/毛盈亏":          _pct_or_na(total_commission, gross_pnl_yuan, loss=True),
        "手续费/净盈亏":          _pct_or_na(total_commission, net_pnl_yuan, loss=True),
        "平均每笔手续费（元）":   f"{avg_commission:.1f}",
        "手续费/盈亏比损失":      f"{plr_loss:.4%}" if not math.isnan(plr_loss) else "N/A",
    }

    return pd.DataFrame([fmt])
