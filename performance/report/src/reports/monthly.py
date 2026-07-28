"""
月度绩效表（两张）

1. build_portfolio  → 组合月度绩效表
2. build_symbol     → 品种月度绩效表

状态判定规则从 config.yaml 读取。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from src import metrics as M


# ── 状态判定 ────────────────────────────────────────────────

def _portfolio_monthly_status(
    monthly_ret: float,
    pos_sym_ratio: float,
    max_cons_loss_days: int,
    max_cons_loss_pct: float,
    prev_status: str,
) -> str:
    if max_cons_loss_days >= 8 or max_cons_loss_pct < -0.05:
        return "严重预警"
    if monthly_ret < 0 and pos_sym_ratio < 0.5:
        if prev_status == "预警":
            return "严重预警"
        return "预警"
    if monthly_ret > 0 and pos_sym_ratio < 0.5:
        return "观察"
    if monthly_ret >= 0 and pos_sym_ratio >= 0.6:
        return "正常"
    return "观察"


def _symbol_monthly_status(
    monthly_ret: float,
    win_rate: float,
    max_drawdown: float,
    prev_status: str,
) -> str:
    if max_drawdown > 0.15 and prev_status == "预警":
        return "严重预警"
    if monthly_ret < 0 and win_rate < 0.5:
        return "预警"
    if monthly_ret <= 0 and win_rate >= 0.4:
        return "观察"
    if monthly_ret > 0 and win_rate >= 0.5:
        return "正常"
    return "观察"


# ── 内部：按月计算指标行 ────────────────────────────────────

def _month_stats(daily_ret: pd.Series) -> dict:
    """月度统计；走 numpy 快速路径。"""
    arr = daily_ret.to_numpy(dtype=np.float64, copy=False) \
        if isinstance(daily_ret, pd.Series) else np.asarray(daily_ret, dtype=np.float64)
    s = M.week_stats_np(arr)
    return dict(
        monthly_ret=s["ret"],
        win_rate=s["win_rate"],
        max_cons_wins=s["max_cons_wins"],
        max_cons_losses=s["max_cons_losses"],
        max_cons_loss_amt=s["max_cons_loss_amt"],
        max_dd=s["max_dd"],
    )


def _monthly_comm_fields(grp: pd.DataFrame, gross_pnl: float, label: str) -> dict:
    """
    计算月度/周度手续费相关的 9 个字段。
    grp 需含 daily_pnl_pct, gross_daily_pnl_pct, daily_pnl, daily_commission, num_trades。
    gross_pnl: 该期间全部交易 pnl 之和（未扣手续费），用于 手续费/毛盈亏。
    label: 前缀，如 '月度' 或 '周度'。
    """
    # 一次性 to_numpy 比反复 grp["..."].sum() 快很多（24k+ 调用）
    daily_pnl_arr   = grp["daily_pnl"].to_numpy(dtype=np.float64, copy=False)
    daily_comm_arr  = grp["daily_commission"].to_numpy(dtype=np.float64, copy=False)
    num_trades_arr  = grp["num_trades"].to_numpy(dtype=np.int64, copy=False)
    gross_daily     = grp["gross_daily_pnl_pct"].to_numpy(dtype=np.float64, copy=False)
    net_daily       = grp["daily_pnl_pct"].to_numpy(dtype=np.float64, copy=False)

    comm_yuan    = float(daily_comm_arr.sum())
    net_pnl_yuan = float(daily_pnl_arr.sum())
    n_trades     = int(num_trades_arr.sum())
    avg_comm     = comm_yuan / n_trades if n_trades > 0 else 0.0

    # 期间收益（不复利 / 加法口径）：固定本金下简单收益直接累加。
    gross_ret = float(gross_daily.sum()) if gross_daily.size else 0.0
    net_ret   = float(net_daily.sum())   if net_daily.size   else 0.0
    gross_win_rate = float((gross_daily > 0).sum() / gross_daily.size) if gross_daily.size else 0.0
    ret_drop = gross_ret - net_ret  # 正值 = 手续费拉低了收益

    def _pct_or_na(num, denom, loss=False):
        if denom <= 0:
            return "N/A（亏损）" if loss else "N/A"
        return f"{num / denom:.4%}"

    return {
        "净盈亏（万元）":            f"{net_pnl_yuan / 10000:.4f}",
        "毛盈亏（万元）":            f"{gross_pnl / 10000:.4f}",
        f"{label}总手续费（万元）":  f"{comm_yuan / 10000:.4f}",
        "手续费/毛盈亏":             _pct_or_na(comm_yuan, gross_pnl, loss=True),
        "手续费/净盈亏":             _pct_or_na(comm_yuan, net_pnl_yuan, loss=True),
        f"毛{label}收益":            f"{gross_ret:.4%}",
        f"毛{label}胜率":            f"{gross_win_rate:.4%}",
        "平均每笔手续费（元）":      f"{avg_comm:.1f}",
        "手续费导致的收益下降":      f"{ret_drop:.4%}",
    }


# ── 组合月度绩效表 ───────────────────────────────────────────

def build_portfolio(
    portfolio_daily: pd.DataFrame,
    symbol_daily: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """
    组合月度绩效表：每年×每月一行。
    """
    df = portfolio_daily.copy()
    df["date"]  = pd.to_datetime(df["date"])
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month

    sym_df = symbol_daily.copy()
    sym_df["date"]  = pd.to_datetime(sym_df["date"])
    sym_df["year"]  = sym_df["date"].dt.year
    sym_df["month"] = sym_df["date"].dt.month

    total_symbols = sym_df["symbol"].nunique()

    tr = trades.copy()
    tr["trade_date"] = pd.to_datetime(tr["trade_date"])
    tr["year"]  = tr["trade_date"].dt.year
    tr["month"] = tr["trade_date"].dt.month
    tr_cnt = tr.groupby(["year", "month"]).size().to_dict()

    rows = []
    prev_status = "正常"
    for (year, month), grp in df.groupby(["year", "month"]):
        daily_ret = grp["daily_pnl_pct"]
        s = _month_stats(daily_ret)

        # 正收益品种占比
        sym_month = sym_df[(sym_df["year"] == year) & (sym_df["month"] == month)]
        sym_ret = sym_month.groupby("symbol")["daily_pnl_pct"].sum()
        pos_sym = int((sym_ret > 0).sum())
        pos_sym_ratio = pos_sym / total_symbols if total_symbols > 0 else 0.0

        status = _portfolio_monthly_status(
            s["monthly_ret"], pos_sym_ratio,
            s["max_cons_losses"], s["max_cons_loss_amt"],
            prev_status,
        )
        prev_status = status

        n_trades = tr_cnt.get((year, month), 0)

        comm_fields = _monthly_comm_fields(
            grp,
            gross_pnl=float(grp["daily_pnl"].sum() + grp["daily_commission"].sum()),
            label="月度",
        )

        row = {
            "年份":               year,
            "月份":               month,
            "月度收益":           f"{s['monthly_ret']:.2%}",
            "月度胜率(交易日)":   f"{s['win_rate']:.2%}",
            "最长连续盈利交易日": f"{s['max_cons_wins']}天",
            "最长连续亏损交易日": f"{s['max_cons_losses']}天",
            "最大连续亏损幅度":   f"{s['max_cons_loss_amt']:.2%}",
            "最大回撤(月内)":     f"{s['max_dd']:.2%}",
            "交易次数":           n_trades,
            "正收益套利对占比":     f"{pos_sym_ratio:.2%}",
            "状态":               status,
        }
        row.update(comm_fields)
        rows.append(row)

    return pd.DataFrame(rows)


# ── 品种月度绩效表 ───────────────────────────────────────────

def build_symbol(
    symbol_daily: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """
    品种月度绩效表：每品种×每年×每月一行。
    额外列：交易次数、正收益月份（累计）、7个手续费字段
    """
    df = symbol_daily.copy()
    df["date"]  = pd.to_datetime(df["date"])
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month

    tr = trades.copy()
    tr["trade_date"] = pd.to_datetime(tr["trade_date"])
    tr["year"]  = tr["trade_date"].dt.year
    tr["month"] = tr["trade_date"].dt.month

    tr_cnt = tr.groupby(["symbol", "year", "month"]).size().to_dict()

    rows = []
    cum_pos_months: dict[str, int] = {}
    prev_status_map: dict[str, str] = {}

    for (symbol, year, month), grp in df.groupby(["symbol", "year", "month"]):
        daily_ret = grp["daily_pnl_pct"]
        s = _month_stats(daily_ret)

        n_trades = tr_cnt.get((symbol, year, month), 0)

        if s["monthly_ret"] > 0:
            cum_pos_months[symbol] = cum_pos_months.get(symbol, 0) + 1
        else:
            cum_pos_months.setdefault(symbol, 0)

        prev_st = prev_status_map.get(symbol, "正常")
        status = _symbol_monthly_status(
            s["monthly_ret"], s["win_rate"], s["max_dd"], prev_st
        )
        prev_status_map[symbol] = status

        comm_fields = _monthly_comm_fields(
            grp,
            gross_pnl=float(grp["daily_pnl"].sum() + grp["daily_commission"].sum()),
            label="月度",
        )

        row = {
            "品种":               symbol,
            "年份":               year,
            "月份":               month,
            "月度收益":           f"{s['monthly_ret']:.2%}",
            "月度胜率(交易日)":   f"{s['win_rate']:.2%}",
            "最长连续盈利交易日": f"{s['max_cons_wins']}天",
            "最长连续亏损交易日": f"{s['max_cons_losses']}天",
            "最大连续亏损幅度":   f"{s['max_cons_loss_amt']:.2%}",
            "最大回撤(月内)":     f"{s['max_dd']:.2%}",
            "交易次数":           n_trades,
            "正收益月份(累计)":   cum_pos_months[symbol],
            "状态":               status,
        }
        row.update(comm_fields)
        rows.append(row)

    return pd.DataFrame(rows)
