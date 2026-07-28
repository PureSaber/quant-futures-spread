"""
周度绩效表（两张）

1. build_portfolio  → 组合周度绩效表
2. build_symbol     → 品种周度绩效表

周次按 ISO 周历（周一为起始日）。
状态判定需要上周状态，通过遍历时序实现。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from src import metrics as M
from src.reports.monthly import _monthly_comm_fields


# ── ISO 周标签 ───────────────────────────────────────────────

def _iso_week_label(date_series: pd.Series) -> pd.Series:
    """返回 (year, week) 元组 Series，按 ISO 周历"""
    dt = pd.to_datetime(date_series)
    return pd.Series(
        list(zip(dt.dt.isocalendar().year, dt.dt.isocalendar().week)),
        index=date_series.index,
    )


def _date_range_str(dates) -> str:
    """``dates`` 已被 build_* 转为 datetime64；这里只取 min/max，避免反复
    ``pd.to_datetime`` + ``sort_values``（24k+ 次循环里成本可观）。"""
    if isinstance(dates, pd.Series):
        arr = dates.to_numpy()
    else:
        arr = np.asarray(dates)
    if arr.size == 0:
        return ""
    first = pd.Timestamp(arr.min())
    last  = pd.Timestamp(arr.max())
    return f"{first.month}/{first.day}-{last.month}/{last.day}"


# ── 状态判定 ────────────────────────────────────────────────

def _portfolio_weekly_status(
    weekly_ret: float,
    pos_sym_ratio: float,
    max_cons_losses: int,
    max_cons_loss_amt: float,
    max_dd: float,
    prev_status: str,
) -> str:
    # 恢复：上周处于预警/严重预警，本周明显好转
    if (prev_status in ("预警", "严重预警") and
            weekly_ret > 0 and pos_sym_ratio >= 0.6):
        return "恢复"
    # 严重预警：上周严重预警且本周未恢复，或触发单周严重阈值
    current_is_warning = (weekly_ret < -0.01 or max_cons_losses >= 4 or
                          max_cons_loss_amt < -0.02 or pos_sym_ratio < 0.3)
    if (weekly_ret < -0.03 or max_dd > 0.05 or pos_sym_ratio < 0.2 or
            (prev_status == "严重预警" and not (weekly_ret > 0 and pos_sym_ratio >= 0.6)) or
            (prev_status == "预警" and current_is_warning)):
        return "严重预警"
    # 预警
    if current_is_warning:
        return "预警"
    # 正常
    if weekly_ret > 0 and pos_sym_ratio >= 0.6 and max_cons_losses <= 2:
        return "正常"
    return "观察"


def _symbol_weekly_status(
    weekly_ret: float,
    win_rate: float,
    max_cons_losses: int,
    max_cons_loss_amt: float,
    max_dd: float,
    prev_status: str,
) -> str:
    if (prev_status in ("预警", "严重预警") and
            weekly_ret > 0 and win_rate >= 0.5):
        return "恢复"
    if (prev_status in ("预警", "严重预警") or
            weekly_ret < -0.03 or max_dd > 0.05):
        return "严重预警"
    if (weekly_ret < -0.01 or max_cons_losses >= 4 or
            max_cons_loss_amt < -0.02 or max_dd > 0.03):
        return "预警"
    if weekly_ret > 0 and win_rate >= 0.5 and max_cons_losses <= 2:
        return "正常"
    return "观察"


# ── 内部：周统计 ────────────────────────────────────────────

def _week_stats(daily_ret: pd.Series) -> dict:
    """周度统计；走 numpy 快速路径，避免 24k+ 次 pandas Series 创建。"""
    arr = daily_ret.to_numpy(dtype=np.float64, copy=False) \
        if isinstance(daily_ret, pd.Series) else np.asarray(daily_ret, dtype=np.float64)
    s = M.week_stats_np(arr)
    return dict(
        weekly_ret=s["ret"],
        win_rate=s["win_rate"],
        max_cons_wins=s["max_cons_wins"],
        max_cons_losses=s["max_cons_losses"],
        max_cons_loss_amt=s["max_cons_loss_amt"],
        max_dd=s["max_dd"],
    )


# ── 组合周度绩效表 ───────────────────────────────────────────

def build_portfolio(
    portfolio_daily: pd.DataFrame,
    symbol_daily: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    df = portfolio_daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["_week"] = _iso_week_label(df["date"])

    sym_df = symbol_daily.copy()
    sym_df["date"]  = pd.to_datetime(sym_df["date"])
    # 用整数列代替元组列，加快后续 == 比较
    _sym_iso = sym_df["date"].dt.isocalendar()
    sym_df["_yr"] = _sym_iso.year.astype(int)
    sym_df["_wk"] = _sym_iso.week.astype(int)

    # 预计算：(year, week) → 该周参与交易的品种数，循环内 O(1) 查找
    tr = trades.copy()
    tr["trade_date"] = pd.to_datetime(tr["trade_date"])
    _tr_iso = tr["trade_date"].dt.isocalendar()
    tr["_iso_yr"] = _tr_iso.year.astype(int)
    tr["_iso_wk"] = _tr_iso.week.astype(int)
    tr_sym_count = (
        tr.groupby(["_iso_yr", "_iso_wk"])["symbol"]
        .nunique()
        .to_dict()
    )
    tr_cnt = tr.groupby(["_iso_yr", "_iso_wk"]).size().to_dict()

    cum_net_by_year: dict[int, float] = {}
    prev_status = "正常"
    rows = []

    for (year, week), grp in df.groupby("_week", sort=True):
        daily_ret = grp["daily_pnl_pct"]
        s = _week_stats(daily_ret)

        # 年初至今累计收益（不复利 / 加法口径）：每个自然年独立重置
        cum_net_by_year[year] = cum_net_by_year.get(year, 0.0) + s["weekly_ret"]
        cum_ret = cum_net_by_year[year]

        # 品种维度（整数列比较，比元组快）
        sym_week = sym_df[(sym_df["_yr"] == year) & (sym_df["_wk"] == week)]
        sym_ret = sym_week.groupby("symbol")["daily_pnl_pct"].sum()
        traded_syms = tr_sym_count.get((year, week), 0)
        n_trades = tr_cnt.get((year, week), 0)
        pos_syms = int((sym_ret > 0).sum())
        pos_ratio = pos_syms / traded_syms if traded_syms > 0 else np.nan

        status = _portfolio_weekly_status(
            s["weekly_ret"], pos_ratio if not np.isnan(pos_ratio) else 0,
            s["max_cons_losses"], s["max_cons_loss_amt"],
            s["max_dd"], prev_status,
        )
        prev_status = status

        comm_fields = _monthly_comm_fields(
            grp,
            gross_pnl=float(grp["daily_pnl"].sum() + grp["daily_commission"].sum()),
            label="周度",
        )

        row = {
            "年份":               year,
            "周次":               week,
            "日期范围":           _date_range_str(grp["date"]),
            "周收益":             f"{s['weekly_ret']:.2%}",
            "周胜率(交易日)":     f"{s['win_rate']:.2%}",
            "最长连续盈利交易日": f"{s['max_cons_wins']}天",
            "最长连续亏损交易日": f"{s['max_cons_losses']}天",
            "最大连续亏损幅度":   f"{s['max_cons_loss_amt']:.2%}",
            "周内最大回撤":       f"{s['max_dd']:.2%}",
            "交易次数":           n_trades,
            "交易套利对数":         traded_syms,
            "正收益套利对数":       pos_syms,
            "正收益套利对占比":     f"{pos_ratio:.2%}" if not np.isnan(pos_ratio) else "N/A",
            "累计周收益(年初至今)": f"{cum_ret:.2%}",
            "状态":               status,
        }
        row.update(comm_fields)
        rows.append(row)

    return pd.DataFrame(rows)


# ── 品种周度绩效表 ───────────────────────────────────────────

def build_symbol(
    symbol_daily: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    df = symbol_daily.copy()
    df["date"]  = pd.to_datetime(df["date"])
    _df_iso = df["date"].dt.isocalendar()
    df["_year"] = _df_iso.year.astype(int)
    df["_wk"]   = _df_iso.week.astype(int)

    # 预计算：(symbol, year, week) → 交易次数，循环内 O(1) 查找
    tr = trades.copy()
    tr["trade_date"] = pd.to_datetime(tr["trade_date"])
    _tr_iso = tr["trade_date"].dt.isocalendar()
    tr["_iso_yr"] = _tr_iso.year.astype(int)
    tr["_iso_wk"] = _tr_iso.week.astype(int)
    tr_cnt = (
        tr.groupby(["symbol", "_iso_yr", "_iso_wk"])
        .size()
        .to_dict()
    )
    rows = []
    prev_status_map: dict[str, str] = {}
    cum_net_map: dict[tuple, float] = {}

    for (symbol, year, week), grp in df.groupby(
        ["symbol", "_year", "_wk"], sort=True
    ):
        daily_ret = grp["daily_pnl_pct"]
        s = _week_stats(daily_ret)

        # 年初至今累计收益（不复利 / 加法口径）：每个自然年独立重置
        key = (symbol, year)
        cum_net_map[key] = cum_net_map.get(key, 0.0) + s["weekly_ret"]
        cum_ret = cum_net_map[key]

        # 交易次数（字典查找）
        n_trades = tr_cnt.get((symbol, year, week), 0)

        prev_st = prev_status_map.get(symbol, "正常")
        status = _symbol_weekly_status(
            s["weekly_ret"], s["win_rate"],
            s["max_cons_losses"], s["max_cons_loss_amt"],
            s["max_dd"], prev_st,
        )
        prev_status_map[symbol] = status

        comm_fields = _monthly_comm_fields(
            grp,
            gross_pnl=float(grp["daily_pnl"].sum() + grp["daily_commission"].sum()),
            label="周度",
        )

        row = {
            "品种":               symbol,
            "年份":               year,
            "周次":               week,
            "日期范围":           _date_range_str(grp["date"]),
            "周收益":             f"{s['weekly_ret']:.2%}",
            "周胜率(交易日)":     f"{s['win_rate']:.2%}",
            "最长连续盈利交易日": f"{s['max_cons_wins']}天",
            "最长连续亏损交易日": f"{s['max_cons_losses']}天",
            "最大连续亏损幅度":   f"{s['max_cons_loss_amt']:.2%}",
            "周内最大回撤":       f"{s['max_dd']:.2%}",
            "交易次数":           n_trades,
            "周收益(年初至今)":   f"{cum_ret:.2%}",
            "状态":               status,
        }
        row.update(comm_fields)
        rows.append(row)

    return pd.DataFrame(rows)
