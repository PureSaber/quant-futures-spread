"""tools/cross_validate_sheets.py —— performance_report 各 sheet 逐字段交叉验证。

独立用 pandas/numpy 按列定义重算（不调用报告内部 metrics），与 Excel 实际值
逐格对照（按显示精度设容差）。覆盖 7 张 sheet 全部数值列与所有行。

用法：python tools/cross_validate_sheets.py boll_grid_A_2020 [--capital 1000000]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from performance.report.loader import prepare_frames  # noqa: E402

RF = 0.02
ANNUAL = 252

SYM = {"日期": "date", "套利对": "spread", "策略": "strategy", "日盈亏": "daily_pnl",
       "日收益率": "daily_pnl_pct", "手续费": "commission", "成交笔数": "num_trades",
       "盈利笔数": "win_trades", "净值": "net_value"}
PORT = {"日期": "date", "策略": "strategy", "套利对数": "num_spreads", "日盈亏": "daily_pnl",
        "日收益率": "daily_pnl_pct", "手续费": "commission", "成交笔数": "num_trades",
        "盈利笔数": "win_trades", "净值": "net_value"}
TR = {"实例ID": "instance_id", "价差合约": "spread", "成交时间": "datetime", "交易日": "trading_day",
      "方向": "direction", "开平": "offset", "成交价": "price", "成交量": "volume", "手续费": "commission"}


# ── 解析 / 比较 ─────────────────────────────────────────────────

def p_pct(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("N/A", "N/A（亏损）", "nan%", "", "-"):
        return float("nan")
    m = re.match(r"^(-?\d+(?:\.\d+)?)%$", s)
    return float(m.group(1)) / 100.0 if m else float("nan")


def p_num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("天", "")
    if s in ("N/A", "N/A（亏损）", "", "-", "nan"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


class C:
    def __init__(self):
        self.n = 0
        self.bad = []

    def eq(self, label, got, exp, tol):
        self.n += 1
        g, e = p_num(got) if not isinstance(got, str) or "%" not in str(got) else p_pct(got), exp
        # 允许两边都是 NaN
        if (isinstance(got, str) and "%" in got):
            g = p_pct(got)
        else:
            g = p_num(got)
        if np.isnan(g) and (e is None or (isinstance(e, float) and np.isnan(e))):
            return
        if e is None or (isinstance(e, float) and np.isnan(e)):
            if not np.isnan(g):
                self.bad.append(f"{label}: excel={got!r} 期望NaN")
            return
        if abs(g - e) > tol:
            self.bad.append(f"{label}: excel={got!r} recompute={e:.6g} |Δ|={abs(g-e):.2g}>tol{tol:g}")

    def eqpct(self, label, got, exp, dp=2):
        self.eq(label, got if isinstance(got, str) and "%" in str(got) else f"{got}", None if exp is None else exp, 0.5 * 10 ** (-dp) + 1e-9)


# ── 指标重算（独立实现，按报告文档口径）──────────────────────────

def m_period_ret(pct):       return float(np.sum(pct))
def m_net(pct):              return 1.0 + np.cumsum(pct)
def m_maxdd(pct):
    net = m_net(pct)
    if len(net) == 0:
        return np.nan
    return float((1 - net / np.maximum.accumulate(net)).max())
def m_ann_ret(pct):
    n = len(pct)
    return float(np.sum(pct) / n * ANNUAL) if n else np.nan
def m_ann_vol(pct):
    pct = np.asarray(pct, float)
    n = len(pct)
    act = pct[pct != 0]
    if act.size < 2 or n <= 0:
        return np.nan
    std = act.std(ddof=1)
    if abs(std) < 1e-10:
        return np.nan
    return float(std * np.sqrt(act.size * ANNUAL / n))
def m_sharpe(pct):
    v = m_ann_vol(pct)
    if v is None or np.isnan(v) or abs(v) < 1e-10:
        return np.nan
    return float((m_ann_ret(pct) - RF) / v)
def m_sortino(pct):
    pct = np.asarray(pct, float)
    n = len(pct)
    act = pct[pct != 0]
    if act.size < 1 or n <= 0:
        return np.nan
    dn = np.minimum(act, 0.0)
    if not (dn < 0).any():
        return np.nan
    dd = float(np.sqrt((dn ** 2).mean()) * np.sqrt(act.size * ANNUAL / n))
    if dd < 1e-12:
        return np.nan
    return float((m_ann_ret(pct) - RF) / dd)
def m_calmar(pct):
    mdd = m_maxdd(pct)
    if mdd == 0 or np.isnan(mdd):
        return np.nan
    return float(m_ann_ret(pct) / mdd)
def m_winday(pct):
    pct = np.asarray(pct, float)
    act = pct[pct != 0]
    return float((act > 0).sum() / act.size) if act.size else np.nan
def m_run(mask):
    mx = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        mx = max(mx, cur)
    return mx
def m_cons_loss_amt(pct):
    pct = np.asarray(pct, float)
    lt = pct < 0
    if not lt.any():
        return 0.0
    worst = cur = 0.0
    inl = False
    for i in range(len(pct)):
        if lt[i]:
            if not inl:
                cur = 0.0; inl = True
            cur += pct[i]
            worst = min(worst, cur)
        else:
            inl = False; cur = 0.0
    return float(worst)
def m_dd_dur(pct):
    net = m_net(pct)
    rm = np.maximum.accumulate(net)
    isdd = net < rm
    mx = cur = 0
    for d in isdd:
        if d:
            cur += 1
        else:
            if cur > 0:
                mx = max(mx, cur + 1)
            cur = 0
    if cur > 0:
        mx = max(mx, cur)
    return mx
def m_plr(profit):
    profit = np.asarray(profit, float)
    w = profit[profit > 0]; l = profit[profit < 0]
    if w.size == 0 or l.size == 0:
        return np.nan
    return float(w.mean() / abs(l.mean()))


def load(run_id, capital):
    base = ROOT / "output" / run_id
    sym_raw = pd.read_csv(base / "daily" / "symbol" / f"daily_pnl_{run_id}.csv").rename(columns=SYM)
    port_raw = pd.read_csv(base / "daily" / "portfolio" / f"daily_pnl_portfolio_{run_id}.csv").rename(columns=PORT)
    fills = pd.read_csv(base / "trades" / "trades.csv").rename(columns=TR)
    n_slots = sym_raw["spread"].nunique()
    total_cap = capital * n_slots
    portfolio, symbol, trades = prepare_frames(
        sym_raw[["date", "spread", "strategy", "daily_pnl", "daily_pnl_pct",
                 "commission", "num_trades", "win_trades"]],
        port_raw, fills, capital, "boll_grid", port_capital=total_cap)
    xlsx = base / "performance" / f"performance_report_{run_id}.xlsx"
    return portfolio, symbol, trades, xlsx, capital, total_cap, n_slots


# ── 各 sheet 校验 ───────────────────────────────────────────────

def check_overview(c, ov, port, trades, total_cap):
    r = ov.iloc[0]
    pct = port["daily_pnl_pct"].to_numpy(float)
    net_pnl = float(port["daily_pnl"].sum())
    comm = float(port["daily_commission"].sum())
    gross_pnl = float(trades["pnl"].sum())
    gross_cum = float(port["gross_daily_pnl_pct"].sum())
    n_tr = len(trades)
    c.eq("核心概览.累计收益率", r["累计收益率"], m_period_ret(pct), 5e-4)
    c.eq("核心概览.年化收益率", r["年化收益率"], m_ann_ret(pct), 5e-4)
    c.eq("核心概览.年化波动率", r["年化波动率"], m_ann_vol(pct), 5e-4)
    c.eq("核心概览.夏普比率", r["夏普比率"], m_sharpe(pct), 5e-3)
    c.eq("核心概览.索提诺比率", r["索提诺比率"], m_sortino(pct), 5e-3)
    c.eq("核心概览.最大回撤", r["最大回撤"], m_maxdd(pct), 5e-4)
    c.eq("核心概览.最大回撤持续天数", r["最大回撤持续天数"], m_dd_dur(pct), 0.5)
    c.eq("核心概览.卡玛比率", r["卡玛比率"], m_calmar(pct), 5e-3)
    c.eq("核心概览.交易日胜率", r["交易日胜率"], m_winday(pct), 5e-4)
    c.eq("核心概览.总交易次数", r["总交易次数"], n_tr, 0.5)
    c.eq("核心概览.按笔胜率", r["按笔胜率"], float((trades["profit"] > 0).sum() / n_tr), 5e-4)
    c.eq("核心概览.盈亏比", r["盈亏比"], m_plr(trades["profit"]), 5e-3)
    c.eq("核心概览.平均每笔收益(元)", r["平均每笔收益(元)"], float(trades["profit"].mean()), 0.5)
    c.eq("核心概览.净盈亏（元）", r["净盈亏（元）"], net_pnl, 1.0)
    c.eq("核心概览.毛盈亏（元）", r["毛盈亏（元）"], gross_pnl, 1.0)
    c.eq("核心概览.总手续费（元）", r["总手续费（元）"], comm, 1.0)
    c.eq("核心概览.毛累计收益率", r["毛累计收益率"], gross_cum, 5e-4)
    c.eq("核心概览.净收益vs毛收益差", r["净收益vs毛收益差"], gross_cum - m_period_ret(pct), 5e-4)
    c.eq("核心概览.平均每笔手续费（元）", r["平均每笔手续费（元）"], comm / n_tr, 0.5)


def check_annual_portfolio(c, yr, port, symbol, trades, total_cap):
    r = yr.iloc[0]
    pct = port["daily_pnl_pct"].to_numpy(float)
    net_pnl = float(port["daily_pnl"].sum())
    comm = float(port["daily_commission"].sum())
    gross_pnl = float(trades["pnl"].sum())
    n_tr = len(trades)
    c.eq("年度_组合.总收益", r["总收益"], m_period_ret(pct), 5e-4)
    c.eq("年度_组合.年化收益", r["年化收益"], m_ann_ret(pct), 5e-4)
    c.eq("年度_组合.夏普比率", r["夏普比率"], m_sharpe(pct), 5e-3)
    c.eq("年度_组合.索提诺比率", r["索提诺比率"], m_sortino(pct), 5e-3)
    c.eq("年度_组合.卡玛比率", r["卡玛比率"], m_calmar(pct), 5e-3)
    c.eq("年度_组合.最大回撤", r["最大回撤"], m_maxdd(pct), 5e-4)
    c.eq("年度_组合.胜率(交易日)", r["胜率(交易日)"], m_winday(pct), 5e-4)
    c.eq("年度_组合.盈亏比", r["盈亏比"], m_plr(trades["profit"]), 5e-3)
    c.eq("年度_组合.交易次数", r["交易次数"], n_tr, 0.5)
    # 品种聚合
    sym_ret = symbol.groupby("symbol")["daily_pnl_pct"].sum()
    c.eq("年度_组合.正收益套利对数", r["正收益套利对数"], float((sym_ret > 0).sum()), 0.5)
    c.eq("年度_组合.总套利对数", r["总套利对数"], float(sym_ret.shape[0]), 0.5)
    c.eq("年度_组合.平均单套利对收益", r["平均单套利对收益"], float(sym_ret.mean()), 5e-4)
    c.eq("年度_组合.净盈亏（万元）", r["净盈亏（万元）"], net_pnl / 1e4, 5e-3)
    c.eq("年度_组合.毛盈亏（万元）", r["毛盈亏（万元）"], gross_pnl / 1e4, 5e-3)
    c.eq("年度_组合.年度总手续费（万元）", r["年度总手续费（万元）"], comm / 1e4, 5e-3)
    c.eq("年度_组合.平均每笔手续费（元）", r["平均每笔手续费（元）"], comm / n_tr, 0.5)
    # 毛年化/毛夏普/夏普下降
    gross_daily = port["gross_daily_pnl_pct"].to_numpy(float)
    gross_ann = m_ann_ret(gross_daily)
    net_vol = m_ann_vol(pct)
    gross_sharpe = (gross_ann - RF) / net_vol if (net_vol and not np.isnan(net_vol)) else np.nan
    c.eq("年度_组合.毛年化收益", r["毛年化收益"], gross_ann, 5e-4)
    c.eq("年度_组合.毛夏普比率", r["毛夏普比率"], gross_sharpe, 5e-3)
    c.eq("年度_组合.手续费导致的夏普下降", r["手续费导致的夏普下降"], gross_sharpe - m_sharpe(pct), 5e-3)


def check_annual_symbol(c, ys, symbol, trades):
    sym_tr = trades.copy()
    sym_tr["date"] = pd.to_datetime(sym_tr["trade_date"])
    for _, r in ys.iterrows():
        s = r["套利对"]
        g = symbol[symbol["symbol"] == s].sort_values("date")
        pct = g["daily_pnl_pct"].to_numpy(float)
        tr = trades[trades["symbol"] == s]
        net_pnl = float(g["daily_pnl"].sum())
        comm = float(g["daily_commission"].sum())
        gross_pnl = float(tr["pnl"].sum())
        n_tr = len(tr)
        pre = f"年度_品种[{s}]."
        c.eq(pre + "总收益", r["总收益"], m_period_ret(pct), 5e-4)
        c.eq(pre + "年化收益", r["年化收益"], m_ann_ret(pct), 5e-4)
        c.eq(pre + "夏普比率", r["夏普比率"], m_sharpe(pct), 5e-3)
        c.eq(pre + "索提诺比率", r["索提诺比率"], m_sortino(pct), 5e-3)
        c.eq(pre + "卡玛比率", r["卡玛比率"], m_calmar(pct), 5e-3)
        c.eq(pre + "最大回撤", r["最大回撤"], m_maxdd(pct), 5e-4)
        c.eq(pre + "胜率(交易日)", r["胜率(交易日)"], m_winday(pct), 5e-4)
        c.eq(pre + "盈亏比", r["盈亏比"], m_plr(tr["profit"]), 5e-3)
        c.eq(pre + "交易次数", r["交易次数"], n_tr, 0.5)
        c.eq(pre + "净盈亏（万元）", r["净盈亏（万元）"], net_pnl / 1e4, 5e-3)
        c.eq(pre + "毛盈亏（万元）", r["毛盈亏（万元）"], gross_pnl / 1e4, 5e-3)
        c.eq(pre + "年度总手续费（万元）", r["年度总手续费（万元）"], comm / 1e4, 5e-3)
        if n_tr:
            c.eq(pre + "平均每笔手续费（元）", r["平均每笔手续费（元）"], comm / n_tr, 0.5)
        # 月度计数：正收益月份数 / 连续亏损月数
        gm = g.copy()
        gm["mon"] = pd.to_datetime(gm["date"]).dt.month
        mret = gm.groupby("mon")["daily_pnl_pct"].sum().sort_index()
        c.eq(pre + "正收益月份数", r["正收益月份数"], float((mret > 0).sum()), 0.5)
        c.eq(pre + "连续亏损月数", r["连续亏损月数"], m_run((mret < 0).to_numpy()), 0.5)


def _ratio_or_na(num, denom):
    return num / denom if denom > 0 else float("nan")


def _period_checks(c, r, pre, pct, gross_pct, net_pnl, comm, gross_pnl, n_tr,
                   ret_key, wr_key, gret_key, gwr_key):
    c.eq(pre + ret_key, r[ret_key], m_period_ret(pct), 5e-4)
    c.eq(pre + wr_key, r[wr_key], m_winday(pct), 5e-4)
    c.eq(pre + "最长连续盈利交易日", r["最长连续盈利交易日"], m_run(pct > 0), 0.5)
    c.eq(pre + "最长连续亏损交易日", r["最长连续亏损交易日"], m_run(pct < 0), 0.5)
    c.eq(pre + "最大连续亏损幅度", r["最大连续亏损幅度"], m_cons_loss_amt(pct), 5e-4)
    c.eq(pre + "交易次数", r["交易次数"], n_tr, 0.5)
    c.eq(pre + "净盈亏（万元）", r["净盈亏（万元）"], net_pnl / 1e4, 5e-3)
    c.eq(pre + "毛盈亏（万元）", r["毛盈亏（万元）"], gross_pnl / 1e4, 5e-3)
    if n_tr:
        c.eq(pre + "平均每笔手续费（元）", r["平均每笔手续费（元）"], comm / n_tr, 0.5)
    # 毛收益率（= Σ gross_daily_pnl_pct，.4%）/ 毛胜率（gross>0 占比，.4%）
    gret = float(np.sum(gross_pct))
    gwr = float((gross_pct > 0).sum() / gross_pct.size) if gross_pct.size else 0.0
    c.eq(pre + gret_key, r[gret_key], gret, 5e-6)
    c.eq(pre + gwr_key, r[gwr_key], gwr, 5e-6)
    c.eq(pre + "手续费导致的收益下降", r["手续费导致的收益下降"], gret - m_period_ret(pct), 5e-6)
    # 手续费/毛盈亏、手续费/净盈亏（亏损则 N/A）
    c.eq(pre + "手续费/毛盈亏", r["手续费/毛盈亏"], _ratio_or_na(comm, gross_pnl), 5e-4)
    c.eq(pre + "手续费/净盈亏", r["手续费/净盈亏"], _ratio_or_na(comm, net_pnl), 5e-4)


def check_monthly(c, mo, frame, trades, by, label, symbol_frame=None, total_symbols=0):
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["y"] = frame["date"].dt.year
    frame["m"] = frame["date"].dt.month
    if symbol_frame is not None:
        symbol_frame = symbol_frame.copy()
        symbol_frame["date"] = pd.to_datetime(symbol_frame["date"])
        symbol_frame["y"] = symbol_frame["date"].dt.year
        symbol_frame["m"] = symbol_frame["date"].dt.month
    tr = trades.copy()
    tr["date"] = pd.to_datetime(tr["trade_date"])
    tr["y"] = tr["date"].dt.year
    tr["m"] = tr["date"].dt.month
    ddkey = "最大回撤(月内)"

    # 月度_品种：正收益月份(累计) 预计算（按品种时间序累加）
    cum_pos = {}
    if by:
        mret = (frame.groupby(["symbol", "y", "m"])["daily_pnl_pct"].sum()
                .reset_index().sort_values(["symbol", "y", "m"]))
        run = {}
        for _, rr in mret.iterrows():
            s = rr["symbol"]
            if rr["daily_pnl_pct"] > 0:
                run[s] = run.get(s, 0) + 1
            else:
                run.setdefault(s, 0)
            cum_pos[(s, int(rr["y"]), int(rr["m"]))] = run[s]
    for _, r in mo.iterrows():
        y, m = int(r["年份"]), int(r["月份"])
        keys = (frame["y"] == y) & (frame["m"] == m)
        tkeys = (tr["y"] == y) & (tr["m"] == m)
        if by:
            keys &= frame["symbol"] == r[by]
            tkeys &= tr["symbol"] == r[by]
        g = frame[keys].sort_values("date")
        pct = g["daily_pnl_pct"].to_numpy(float)
        gpct = g["gross_daily_pnl_pct"].to_numpy(float)
        tg = tr[tkeys]
        pre = f"{label}[{y}-{m}{('-'+str(r[by])) if by else ''}]."
        _period_checks(c, r, pre, pct, gpct, float(g["daily_pnl"].sum()),
                       float(g["daily_commission"].sum()), float(tg["pnl"].sum()),
                       len(tg), "月度收益", "月度胜率(交易日)", "毛月度收益", "毛月度胜率")
        c.eq(pre + ddkey, r[ddkey], m_maxdd(pct), 5e-4)
        if by is None and symbol_frame is not None:
            sm = symbol_frame[(symbol_frame["y"] == y) & (symbol_frame["m"] == m)]
            sym_ret = sm.groupby("symbol")["daily_pnl_pct"].sum()
            ratio = float((sym_ret > 0).sum() / total_symbols)
            c.eq(pre + "正收益套利对占比", r["正收益套利对占比"], ratio, 5e-4)
        if by:
            c.eq(pre + "正收益月份(累计)", r["正收益月份(累计)"],
                 cum_pos.get((r[by], y, m), 0), 0.5)


def check_weekly(c, wk, frame, trades, by, label, symbol_frame=None):
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    iso = frame["date"].dt.isocalendar()
    frame["y"] = iso.year.astype(int)
    frame["w"] = iso.week.astype(int)
    if symbol_frame is not None:
        symbol_frame = symbol_frame.copy()
        symbol_frame["date"] = pd.to_datetime(symbol_frame["date"])
        siso = symbol_frame["date"].dt.isocalendar()
        symbol_frame["y"] = siso.year.astype(int)
        symbol_frame["w"] = siso.week.astype(int)
    tr = trades.copy()
    tr["date"] = pd.to_datetime(tr["trade_date"])
    tiso = tr["date"].dt.isocalendar()
    tr["y"] = tiso.year.astype(int)
    tr["w"] = tiso.week.astype(int)
    ddkey = "周内最大回撤"
    ytd = {}        # 年初至今累计（组合或品种）
    for _, r in wk.iterrows():
        y, w = int(r["年份"]), int(r["周次"])
        keys = (frame["y"] == y) & (frame["w"] == w)
        tkeys = (tr["y"] == y) & (tr["w"] == w)
        if by:
            keys &= frame["symbol"] == r[by]
            tkeys &= tr["symbol"] == r[by]
        g = frame[keys].sort_values("date")
        pct = g["daily_pnl_pct"].to_numpy(float)
        gpct = g["gross_daily_pnl_pct"].to_numpy(float)
        tg = tr[tkeys]
        pre = f"{label}[{y}w{w}{('-'+str(r[by])) if by else ''}]."
        _period_checks(c, r, pre, pct, gpct, float(g["daily_pnl"].sum()),
                       float(g["daily_commission"].sum()), float(tg["pnl"].sum()),
                       len(tg), "周收益", "周胜率(交易日)", "毛周度收益", "毛周度胜率")
        c.eq(pre + ddkey, r[ddkey], m_maxdd(pct), 5e-4)
        # 年初至今累计（加法，按自然年重置）
        yk = (y, r[by]) if by else y
        ytd[yk] = ytd.get(yk, 0.0) + m_period_ret(pct)
        ytd_key = "周收益(年初至今)" if by else "累计周收益(年初至今)"
        c.eq(pre + ytd_key, r[ytd_key], ytd[yk], 5e-4)
        # 组合周度：交易套利对数 / 正收益套利对数 / 占比
        if by is None and symbol_frame is not None:
            sm = symbol_frame[(symbol_frame["y"] == y) & (symbol_frame["w"] == w)]
            sym_ret = sm.groupby("symbol")["daily_pnl_pct"].sum()
            traded = tg["symbol"].nunique()
            pos = int((sym_ret > 0).sum())
            c.eq(pre + "交易套利对数", r["交易套利对数"], traded, 0.5)
            c.eq(pre + "正收益套利对数", r["正收益套利对数"], pos, 0.5)
            ratio = pos / traded if traded > 0 else float("nan")
            c.eq(pre + "正收益套利对占比", r["正收益套利对占比"], ratio, 5e-4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    args = ap.parse_args()

    portfolio, symbol, trades, xlsx, cap, total_cap, n_slots = load(args.run_id, args.capital)
    rd = lambda sh: pd.read_excel(xlsx, sheet_name=sh)

    c = C()
    check_overview(c, rd("核心概览"), portfolio, trades, total_cap)
    print(f"核心概览        : {c.n} 项已校验, 累计失败 {len(c.bad)}")
    check_annual_portfolio(c, rd("年度_组合"), portfolio, symbol, trades, total_cap)
    print(f"+年度_组合      : 累计 {c.n} 项, 失败 {len(c.bad)}")
    check_annual_symbol(c, rd("年度_品种"), symbol, trades)
    print(f"+年度_品种(9)   : 累计 {c.n} 项, 失败 {len(c.bad)}")
    check_monthly(c, rd("月度_组合"), portfolio, trades, None, "月度_组合",
                  symbol_frame=symbol, total_symbols=n_slots)
    print(f"+月度_组合(12)  : 累计 {c.n} 项, 失败 {len(c.bad)}")
    check_monthly(c, rd("月度_品种"), symbol, trades, "套利对", "月度_品种")
    print(f"+月度_品种(36)  : 累计 {c.n} 项, 失败 {len(c.bad)}")
    check_weekly(c, rd("周度_组合"), portfolio, trades, None, "周度_组合", symbol_frame=symbol)
    print(f"+周度_组合(52)  : 累计 {c.n} 项, 失败 {len(c.bad)}")
    check_weekly(c, rd("周度_品种"), symbol, trades, "套利对", "周度_品种")
    print(f"+周度_品种(158) : 累计 {c.n} 项, 失败 {len(c.bad)}")

    print("\n" + "=" * 60)
    if c.bad:
        print(f"[FAIL] {len(c.bad)}/{c.n} 字段不一致：")
        for b in c.bad[:60]:
            print("  -", b)
        if len(c.bad) > 60:
            print(f"  ... 其余 {len(c.bad)-60} 条略")
        sys.exit(1)
    print(f"[ok  ] 全部 {c.n} 个字段逐格交叉验证通过")


if __name__ == "__main__":
    main()
