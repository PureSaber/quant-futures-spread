"""tools/cross_validate_chain.py —— 回测产出全链路交叉校验。

链路：signals → trades → daily/symbol → daily/portfolio → performance。
无滑点假设下逐层对账，并与 performance 报告交叉验证。

用法：python tools/cross_validate_chain.py boll_grid_A_2020 [--capital 1000000]
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

from performance import summarize  # noqa: E402

PASS = "[ok  ]"
FAIL = "[FAIL]"


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.n_checks = 0

    def check(self, cond: bool, label: str, detail: str = "") -> None:
        self.n_checks += 1
        if cond:
            print(f"  {PASS} {label}")
        else:
            msg = f"{label}" + (f" | {detail}" if detail else "")
            self.errors.append(msg)
            print(f"  {FAIL} {label}  ->  {detail}")


# ── 字符串解析（Excel 单元格是格式化字符串）────────────────────────

def parse_pct(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    m = re.match(r"^(-?\d+(?:\.\d+)?)%$", s)
    return float(m.group(1)) / 100.0 if m else float("nan")


def parse_num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")


# ── 列名映射 ────────────────────────────────────────────────────

SYM = {"日期": "date", "套利对": "spread", "策略": "strategy", "日盈亏": "pnl",
       "日收益率": "pct", "手续费": "comm", "成交笔数": "nt", "盈利笔数": "wt", "净值": "nv"}
PORT = {"日期": "date", "策略": "strategy", "套利对数": "ns", "日盈亏": "pnl",
        "日收益率": "pct", "手续费": "comm", "成交笔数": "nt", "盈利笔数": "wt", "净值": "nv"}
TR = {"实例ID": "iid", "价差合约": "spread", "成交时间": "dt", "交易日": "td",
      "方向": "dir", "开平": "off", "成交价": "price", "成交量": "vol", "手续费": "comm"}

# trade(方向,开平) -> signal(offset, direction)
TRADE_TO_SIGNAL = {
    ("LONG", "OPEN"): ("open", 1),
    ("SHORT", "OPEN"): ("open", -1),
    ("SHORT", "CLOSE"): ("close", -1),
    ("LONG", "CLOSE"): ("close", 1),
}


def load(run_id: str):
    base = ROOT / "output" / run_id
    sig = pd.read_csv(base / "signals" / f"signals_{run_id}.csv")
    tr = pd.read_csv(base / "trades" / "trades.csv").rename(columns=TR)
    sym = pd.read_csv(base / "daily" / "symbol" / f"daily_pnl_{run_id}.csv").rename(columns=SYM)
    port = pd.read_csv(base / "daily" / "portfolio" / f"daily_pnl_portfolio_{run_id}.csv").rename(columns=PORT)
    summary = pd.read_csv(base / "performance" / "summary.csv", index_col=0)
    xlsx = base / "performance" / f"performance_report_{run_id}.xlsx"
    for df, col in ((tr, "td"), (sym, "date"), (port, "date")):
        df[col] = pd.to_datetime(df[col])
    sig["tradingday"] = pd.to_datetime(sig["tradingday"])
    return sig, tr, sym, port, summary, xlsx


# ── Round 1: signals ↔ trades ───────────────────────────────────

def round1(c: Checker, sig: pd.DataFrame, tr: pd.DataFrame) -> None:
    print("\n=== Round 1: signals ↔ trades（无滑点：成交价∈信号价集合）===")
    sig_prices: dict[tuple, set] = {}
    for _, r in sig.iterrows():
        key = (r["symbol"], r["offset"], int(r["direction"]))
        sig_prices.setdefault(key, set()).add(round(float(r["price"]), 4))

    miss = []
    for _, t in tr.iterrows():
        off, d = TRADE_TO_SIGNAL[(t["dir"], t["off"])]
        key = (t["spread"], off, d)
        if round(float(t["price"]), 4) not in sig_prices.get(key, set()):
            miss.append((t["spread"], t["dir"], t["off"], t["price"]))
    c.check(not miss, f"每笔成交价都能在信号价集合中找到（{len(tr)} 笔）",
            f"{len(miss)} 笔无对应信号，例: {miss[:3]}")

    bad_dir = tr[~tr.apply(lambda t: (t["dir"], t["off"]) in TRADE_TO_SIGNAL, axis=1)]
    c.check(bad_dir.empty, "成交方向/开平组合合法", f"{len(bad_dir)} 行非法")

    # 价差合约集合一致
    sig_syms = set(sig["symbol"].unique())
    tr_syms = set(tr["spread"].unique())
    c.check(tr_syms <= sig_syms, "成交涉及的套利对都有信号记录",
            f"无信号的套利对: {tr_syms - sig_syms}")


# ── Round 2: trades ↔ symbol/daily_pnl ──────────────────────────

def round2(c: Checker, tr: pd.DataFrame, sym: pd.DataFrame) -> None:
    print("\n=== Round 2: trades ↔ symbol/daily_pnl ===")
    tr_comm = tr.groupby([tr["td"], "spread"])["comm"].sum()
    tr_cnt = tr.groupby([tr["td"], "spread"]).size()
    sym_idx = sym.set_index(["date", "spread"])

    comm_bad = cnt_bad = 0
    for (d, s), grp in sym_idx.groupby(level=[0, 1]):
        scomm = float(grp["comm"].iloc[0])
        snt = int(grp["nt"].iloc[0])
        tc = float(tr_comm.get((d, s), 0.0))
        tn = int(tr_cnt.get((d, s), 0))
        if abs(scomm - tc) > 0.01:
            comm_bad += 1
        if snt != tn:
            cnt_bad += 1
    c.check(comm_bad == 0, "symbol 手续费 == 当日该套利对成交手续费之和", f"{comm_bad} 处不符")
    c.check(cnt_bad == 0, "symbol 成交笔数 == 当日该套利对成交笔数", f"{cnt_bad} 处不符")

    # 每笔手续费 = 每手成本 × 手数；比例费/平今允许每手成本随成交变化
    tr2 = tr.copy()
    tr2["cpl"] = tr2["comm"] / tr2["vol"]
    c.check((tr2["cpl"] > 0).all(), "每笔手续费为正",
            f"非正: {tr2[tr2['cpl'] <= 0].head().to_dict()}")

    # 总量对账
    c.check(abs(tr["comm"].sum() - sym["comm"].sum()) < 0.05,
            "总手续费 trades == symbol", f"{tr['comm'].sum()} vs {sym['comm'].sum()}")
    c.check(int(len(tr)) == int(sym["nt"].sum()),
            "总成交笔数 trades == symbol", f"{len(tr)} vs {int(sym['nt'].sum())}")


# ── Round 3: symbol ↔ portfolio ─────────────────────────────────

def round3(c: Checker, sym: pd.DataFrame, port: pd.DataFrame, n_slots: int) -> None:
    print(f"\n=== Round 3: symbol ↔ portfolio（N={n_slots} 槽位）===")
    g = sym.groupby("date")
    exp = pd.DataFrame({
        "pnl": g["pnl"].sum(),
        "pct": g["pct"].sum() / n_slots,
        "comm": g["comm"].sum(),
        "nt": g["nt"].sum().astype(int),
        "wt": g["wt"].sum().astype(int),
        "ns": g["spread"].count().astype(int),
    })
    p = port.set_index("date")

    c.check((p["pnl"] - exp["pnl"]).abs().max() < 0.05, "日盈亏 = Σ symbol 日盈亏",
            f"max|Δ|={(p['pnl']-exp['pnl']).abs().max()}")
    c.check((p["pct"] - exp["pct"]).abs().max() < 1e-7, "日收益率 = Σ symbol 收益率 / N",
            f"max|Δ|={(p['pct']-exp['pct']).abs().max()}")
    c.check((p["comm"] - exp["comm"]).abs().max() < 0.05, "手续费 = Σ symbol", "")
    c.check((p["nt"] - exp["nt"]).abs().max() == 0, "成交笔数 = Σ symbol", "")
    c.check((p["wt"] - exp["wt"]).abs().max() == 0, "盈利笔数 = Σ symbol", "")
    c.check((p["ns"] - exp["ns"]).abs().max() == 0, "套利对数 = 当日 symbol 行数", "")

    # 净值加性：portfolio
    nav = 1.0
    expnv = []
    for x in p["pct"].fillna(0):
        nav += round(float(x), 8)
        expnv.append(round(nav, 4))
    c.check(np.abs(p["nv"].to_numpy() - np.array(expnv)).max() < 1e-4,
            "portfolio 净值 = 1 + Σ日收益率（加性）", "")

    # 净值加性：每个 symbol
    bad = []
    for s, grp in sym.groupby("spread"):
        grp = grp.sort_values("date")
        nav = 1.0
        expnv = []
        for x in grp["pct"].fillna(0):
            nav += round(float(x), 8)
            expnv.append(round(nav, 4))
        if np.abs(grp["nv"].to_numpy() - np.array(expnv)).max() > 1e-4:
            bad.append(s)
    c.check(not bad, "每个套利对净值 = 1 + Σ日收益率", f"异常: {bad}")


# ── Round 4: performance/summary ↔ portfolio ────────────────────

def round4(c: Checker, port: pd.DataFrame, summary: pd.DataFrame,
           tr: pd.DataFrame, run_id: str, capital: float) -> None:
    print("\n=== Round 4: performance/summary ↔ portfolio ===")
    pct = port["pct"].astype(float).fillna(0.0)
    s = summarize(pct, capital)
    row = summary.loc[run_id]
    pairs = [("累计收益率", "total_return"), ("年化收益率", "annual_return"),
             ("夏普比率", "sharpe"), ("最大回撤", "max_drawdown"), ("胜率", "win_rate")]
    for cn, en in pairs:
        a, b = float(row[cn]), s[en]
        c.check(abs(a - b) < 1e-4, f"summary.{cn} 复算一致", f"csv={a} recomputed={b}")
    c.check(int(row["成交笔数"]) == int(port["nt"].sum()) == len(tr),
            "summary.成交笔数 == portfolio == trades 行数",
            f"{int(row['成交笔数'])}/{int(port['nt'].sum())}/{len(tr)}")
    c.check(abs(float(row["累计手续费"]) - port["comm"].sum()) < 0.05,
            "summary.累计手续费 == Σ portfolio 手续费", "")
    c.check(int(row["交易日数"]) == len(port), "summary.交易日数 == portfolio 行数", "")


# ── Round 5: Excel 报告 ↔ CSV 汇总 ───────────────────────────────

def round5(c: Checker, xlsx: Path, port: pd.DataFrame, sym: pd.DataFrame,
           tr: pd.DataFrame, total_capital: float) -> None:
    print("\n=== Round 5: performance_report Excel ↔ CSV 汇总 ===")
    if not xlsx.exists():
        c.check(False, "performance_report.xlsx 存在", str(xlsx))
        return

    net_pnl = float(port["pnl"].sum())
    comm = float(port["comm"].sum())
    total_ret = float(port["pct"].sum())

    ov = pd.read_excel(xlsx, sheet_name="核心概览").iloc[0]
    c.check(abs(parse_num(ov["净盈亏（元）"]) - net_pnl) < 1.0,
            "核心概览.净盈亏 == Σ portfolio 日盈亏", f"{ov['净盈亏（元）']} vs {net_pnl:.0f}")
    c.check(abs(parse_num(ov["总手续费（元）"]) - comm) < 1.0,
            "核心概览.总手续费 == Σ portfolio 手续费", f"{ov['总手续费（元）']} vs {comm:.0f}")
    c.check(abs(parse_pct(ov["累计收益率"]) - total_ret) < 5e-4,
            "核心概览.累计收益率 == Σ日收益率", f"{ov['累计收益率']} vs {total_ret:.4f}")
    c.check(abs(parse_pct(ov["毛累计收益率"]) - (net_pnl + comm) / total_capital) < 5e-4,
            "核心概览.毛累计收益率 == (净盈亏+手续费)/总本金",
            f"{ov['毛累计收益率']} vs {(net_pnl+comm)/total_capital:.4f}")
    c.check(int(parse_num(ov["总交易次数"])) == len(tr),
            "核心概览.总交易次数 == trades 行数", f"{ov['总交易次数']} vs {len(tr)}")

    # 年度_组合：单一年份 → 总额对账
    yr = pd.read_excel(xlsx, sheet_name="年度_组合")
    c.check(abs(parse_num(yr["净盈亏（万元）"].sum()) * 10000 - net_pnl) < 100,
            "年度_组合.净盈亏(万元)×1e4 求和 == Σ portfolio 日盈亏",
            f"{parse_num(yr['净盈亏（万元）'].sum())*1e4:.0f} vs {net_pnl:.0f}")
    yr_ret = sum(parse_pct(v) for v in yr["总收益"])
    c.check(abs(yr_ret - total_ret) < 1e-3,
            "年度_组合.总收益求和 == 累计收益率", f"{yr_ret:.4f} vs {total_ret:.4f}")

    # 月度_组合：净盈亏求和、收益求和
    mo = pd.read_excel(xlsx, sheet_name="月度_组合")
    mo_pnl = sum(parse_num(v) for v in mo["净盈亏（万元）"]) * 10000
    c.check(abs(mo_pnl - net_pnl) < 100,
            "月度_组合.净盈亏求和 == Σ portfolio 日盈亏", f"{mo_pnl:.0f} vs {net_pnl:.0f}")

    # 周度_组合：净盈亏求和
    wk = pd.read_excel(xlsx, sheet_name="周度_组合")
    if "净盈亏（万元）" in wk.columns:
        wk_pnl = sum(parse_num(v) for v in wk["净盈亏（万元）"]) * 10000
        c.check(abs(wk_pnl - net_pnl) < 200,
                "周度_组合.净盈亏求和 == Σ portfolio 日盈亏", f"{wk_pnl:.0f} vs {net_pnl:.0f}")

    # 年度_品种：各套利对净盈亏求和 == 组合
    ys = pd.read_excel(xlsx, sheet_name="年度_品种")
    if "净盈亏（万元）" in ys.columns:
        ys_pnl = sum(parse_num(v) for v in ys["净盈亏（万元）"]) * 10000
        c.check(abs(ys_pnl - net_pnl) < 100,
                "年度_品种.净盈亏求和 == 组合净盈亏", f"{ys_pnl:.0f} vs {net_pnl:.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    args = ap.parse_args()

    sig, tr, sym, port, summary, xlsx = load(args.run_id)
    n_slots = sym["spread"].nunique()
    total_capital = args.capital * n_slots

    c = Checker()
    round1(c, sig, tr)
    round2(c, tr, sym)
    round3(c, sym, port, n_slots)
    round4(c, port, summary, tr, args.run_id, args.capital)
    round5(c, xlsx, port, sym, tr, total_capital)

    print("\n" + "=" * 60)
    if c.errors:
        print(f"{FAIL} {len(c.errors)}/{c.n_checks} 项不通过：")
        for e in c.errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"{PASS} 全部 {c.n_checks} 项交叉校验通过")


if __name__ == "__main__":
    main()
