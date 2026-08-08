"""
年度绩效表（两张）

1. build_portfolio  → 综合年度绩效表（每年一行，组合维度）
2. build_symbol     → 品种年度绩效表（每年×每品种一行）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from performance.report.report_lib import metrics as M


# ── 内部辅助 ────────────────────────────────────────────────

def _year_metrics(
    daily_returns: pd.Series,
    trades_profit: pd.Series,
    rf: float,
    annual: int,
) -> dict:
    """给定某年的日收益序列和交易盈亏序列，计算全套年度指标。"""
    trading_days = len(daily_returns)
    net_value = M.net_value_series(daily_returns)

    total_ret = M.cumulative_return(net_value)
    ann_ret   = M.annualized_return(net_value, trading_days, annual)
    sharpe    = M.sharpe_ratio(daily_returns, trading_days, rf, annual)
    sortino   = M.sortino_ratio(daily_returns, trading_days, rf, annual)
    calmar    = M.calmar_ratio(net_value, trading_days, annual)
    mdd       = M.max_drawdown(net_value)
    day_wr    = M.win_rate_by_day(daily_returns)
    plr       = M.profit_loss_ratio(trades_profit) if not trades_profit.empty else np.nan
    n_trades  = len(trades_profit)

    return dict(
        总收益=total_ret,
        年化收益=ann_ret,
        夏普比率=sharpe,
        索提诺比率=sortino,
        卡玛比率=calmar,
        最大回撤=mdd,
        胜率_交易日=day_wr,
        盈亏比=plr,
        交易次数=n_trades,
    )


def _annual_commission_fields(
    grp: pd.DataFrame,
    gross_pnl: float,
    rf: float,
    annual: int,
    net_sharpe: float,
) -> dict:
    """
    计算年度手续费相关的 9 个字段。
    grp 必须包含 daily_pnl_pct, gross_daily_pnl_pct, daily_pnl, daily_commission, num_trades。
    gross_pnl: 该年毛盈亏（元）= daily_pnl + daily_commission 之和。
    """
    comm_yuan = float(grp["daily_commission"].sum())
    net_pnl_yuan = float(grp["daily_pnl"].sum())
    n_trades = int(grp["num_trades"].sum())
    avg_comm = comm_yuan / n_trades if n_trades > 0 else 0.0

    # 毛年化收益
    gross_daily = grp["gross_daily_pnl_pct"]
    trading_days = len(gross_daily)
    gross_nv = M.net_value_series(gross_daily)
    gross_ann_ret = M.annualized_return(gross_nv, trading_days, annual)

    # 毛夏普：净年化波动率作分母
    net_vol = M.annualized_volatility(grp["daily_pnl_pct"], annual)
    if not np.isnan(net_vol) and net_vol > 1e-10:
        gross_sharpe = (gross_ann_ret - rf) / net_vol
    else:
        gross_sharpe = np.nan

    sharpe_drop = gross_sharpe - net_sharpe if not (
        np.isnan(gross_sharpe) or np.isnan(net_sharpe)
    ) else np.nan

    def _pct_or_na(num, denom, loss=False):
        if denom <= 0:
            return "N/A（亏损）" if loss else "N/A"
        return f"{num / denom:.4%}"

    return {
        "净盈亏（万元）":           f"{net_pnl_yuan / 10000:.4f}",
        "毛盈亏（万元）":           f"{gross_pnl / 10000:.4f}",
        "年度总手续费（万元）":     f"{comm_yuan / 10000:.4f}",
        "手续费/毛盈亏":            _pct_or_na(comm_yuan, gross_pnl, loss=True),
        "手续费/净盈亏":            _pct_or_na(comm_yuan, net_pnl_yuan, loss=True),
        "毛年化收益":               f"{gross_ann_ret:.2%}" if not np.isnan(gross_ann_ret) else "N/A",
        "毛夏普比率":               f"{gross_sharpe:.2f}" if not np.isnan(gross_sharpe) else "N/A",
        "手续费导致的夏普下降":     f"{sharpe_drop:.2f}" if not np.isnan(sharpe_drop) else "N/A",
        "平均每笔手续费（元）":     f"{avg_comm:.1f}",
    }


# ── 综合年度绩效表 ───────────────────────────────────────────

def build_portfolio(
    portfolio_daily: pd.DataFrame,
    symbol_daily: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """
    综合年度绩效表：每年一行，组合维度。

    额外列：正收益品种数、总品种数、正收益品种占比、平均单品种收益、7个手续费字段
    """
    rf = config.get("risk_free_rate", 0.02)
    annual = config.get("trading_days_per_year", 252)

    portfolio_daily = portfolio_daily.copy()
    portfolio_daily["year"] = pd.to_datetime(portfolio_daily["date"]).dt.year

    # 预计算：year → profit Series（按笔盈亏比等仍用成交记录）
    tr = trades.copy()
    tr["year"] = pd.to_datetime(tr["trade_date"]).dt.year
    tr_profit_by_year = {yr: grp["profit"].reset_index(drop=True)
                         for yr, grp in tr.groupby("year")}

    sym_year_df = symbol_daily.copy()
    sym_year_df["year"] = pd.to_datetime(sym_year_df["date"]).dt.year

    rows = []
    for year, grp in portfolio_daily.groupby("year"):
        daily_ret = grp["daily_pnl_pct"]
        year_trades = tr_profit_by_year.get(year, pd.Series(dtype=float))

        m = _year_metrics(daily_ret, year_trades, rf, annual)

        # 品种层面：该年各品种累计收益（不复利 / 加法口径）
        sym_grp = (
            sym_year_df[sym_year_df["year"] == year]
            .groupby("symbol")["daily_pnl_pct"]
            .apply(lambda s: float(s.sum()))
        )
        total_symbols = sym_grp.shape[0]
        pos_symbols = int((sym_grp > 0).sum())
        avg_sym_ret = float(sym_grp.mean()) if total_symbols > 0 else np.nan

        comm_fields = _annual_commission_fields(
            grp,
            gross_pnl=float(grp["daily_pnl"].sum() + grp["daily_commission"].sum()),
            rf=rf, annual=annual,
            net_sharpe=m["夏普比率"],
        )

        row = {
            "年份":           year,
            "总收益":         f"{m['总收益']:.2%}",
            "年化收益":       f"{m['年化收益']:.2%}",
            "夏普比率":       f"{m['夏普比率']:.2f}",
            "索提诺比率":     f"{m['索提诺比率']:.2f}",
            "卡玛比率":       f"{m['卡玛比率']:.2f}",
            "最大回撤":       f"{m['最大回撤']:.2%}",
            "胜率(交易日)":   f"{m['胜率_交易日']:.2%}",
            "盈亏比":         f"{m['盈亏比']:.2f}" if not np.isnan(m["盈亏比"]) else "N/A",
            "交易次数":       m["交易次数"],
            "正收益套利对数":   pos_symbols,
            "总套利对数":       total_symbols,
            "正收益套利对占比": f"{pos_symbols / total_symbols:.2%}" if total_symbols > 0 else "N/A",
            "平均单套利对收益": f"{avg_sym_ret:.2%}" if not np.isnan(avg_sym_ret) else "N/A",
        }
        row.update(comm_fields)
        rows.append(row)

    return pd.DataFrame(rows)


# ── 品种年度绩效表 ───────────────────────────────────────────

def build_symbol(
    symbol_daily: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """
    品种年度绩效表：每年×每品种一行。

    额外列：正收益月份数、连续亏损月数、7个手续费字段
    """
    rf = config.get("risk_free_rate", 0.02)
    annual = config.get("trading_days_per_year", 252)

    symbol_daily = symbol_daily.copy()
    symbol_daily["year"]  = pd.to_datetime(symbol_daily["date"]).dt.year
    symbol_daily["month"] = pd.to_datetime(symbol_daily["date"]).dt.month

    # 预计算：(symbol, year) → profit Series
    tr = trades.copy()
    tr["year"] = pd.to_datetime(tr["trade_date"]).dt.year
    tr_profit_by_sym_year = {
        (sym, yr): grp["profit"].reset_index(drop=True)
        for (sym, yr), grp in tr.groupby(["symbol", "year"])
    }

    rows = []
    for (symbol, year), grp in symbol_daily.groupby(["symbol", "year"]):
        daily_ret = grp["daily_pnl_pct"]
        sym_trades = tr_profit_by_sym_year.get((symbol, year), pd.Series(dtype=float))

        m = _year_metrics(daily_ret, sym_trades, rf, annual)

        # 正收益月份数 / 连续亏损月数
        monthly_ret = grp.groupby("month")["daily_pnl_pct"].sum()
        pos_months  = M.positive_months(monthly_ret)
        cons_loss_m = M.max_consecutive_loss_months(monthly_ret)

        comm_fields = _annual_commission_fields(
            grp,
            gross_pnl=float(grp["daily_pnl"].sum() + grp["daily_commission"].sum()),
            rf=rf, annual=annual,
            net_sharpe=m["夏普比率"],
        )

        row = {
            "品种":           symbol,
            "年份":           year,
            "总收益":         f"{m['总收益']:.2%}",
            "年化收益":       f"{m['年化收益']:.2%}",
            "夏普比率":       f"{m['夏普比率']:.2f}",
            "索提诺比率":     f"{m['索提诺比率']:.2f}",
            "卡玛比率":       f"{m['卡玛比率']:.2f}",
            "最大回撤":       f"{m['最大回撤']:.2%}",
            "胜率(交易日)":   f"{m['胜率_交易日']:.2%}",
            "盈亏比":         f"{m['盈亏比']:.2f}" if not np.isnan(m["盈亏比"]) else "N/A",
            "交易次数":       m["交易次数"],
            "正收益月份数":   pos_months,
            "连续亏损月数":   cons_loss_m,
        }
        row.update(comm_fields)
        rows.append(row)

    return pd.DataFrame(rows)
