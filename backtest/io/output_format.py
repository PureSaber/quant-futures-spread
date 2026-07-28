"""backtest/io/output_format.py — 产出 CSV 列名与小数位。"""
from __future__ import annotations

import pandas as pd

# daily/portfolio —— 策略整体净值（对齐 CTA daily/portfolio，无品种列）
PORTFOLIO_COL_CN = {
    "date": "日期",
    "strategy": "策略",
    "num_spreads": "套利对数",
    "daily_pnl": "日盈亏",
    "daily_pnl_pct": "日收益率",
    "commission": "手续费",
    "num_trades": "成交笔数",
    "win_trades": "盈利笔数",
    "net_value": "净值",
}
PORTFOLIO_COL_ORDER = list(PORTFOLIO_COL_CN.keys())

# daily/symbol —— 各套利对净值（对齐 CTA daily/symbol，列名用套利对）
SPREAD_COL_CN = {
    "date": "日期",
    "spread": "套利对",
    "strategy": "策略",
    "daily_pnl": "日盈亏",
    "daily_pnl_pct": "日收益率",
    "commission": "手续费",
    "num_trades": "成交笔数",
    "win_trades": "盈利笔数",
    "net_value": "净值",
}
SPREAD_COL_ORDER = list(SPREAD_COL_CN.keys())

TRADES_COL_CN = {
    "instance_id": "实例ID",
    "spread": "价差合约",
    "datetime": "成交时间",
    "trading_day": "交易日",
    "direction": "方向",
    "offset": "开平",
    "price": "成交价",
    "volume": "成交量",
    "commission": "手续费",
}

SUMMARY_INDEX_CN = "回测ID"
SUMMARY_COL_CN = {
    "days": "交易日数",
    "active_days": "活跃天数",
    "total_return": "累计收益率",
    "annual_return": "年化收益率",
    "sharpe": "夏普比率",
    "max_drawdown": "最大回撤",
    "calmar": "卡玛比率",
    "win_rate": "胜率",
    "num_trades": "成交笔数",
    "total_commission": "累计手续费",
}

DECIMALS = {
    "daily_pnl": 2,
    "daily_pnl_pct": 8,
    "commission": 2,
    "net_value": 4,
    "price": 2,
    "total_return": 6,
    "annual_return": 6,
    "sharpe": 4,
    "max_drawdown": 6,
    "calmar": 4,
    "win_rate": 4,
    "total_commission": 2,
    "days": 0,
    "active_days": 0,
    "num_trades": 0,
    "num_spreads": 0,
}


def _fmt_float(v: float, nd: int) -> str:
    return f"{float(v):.{nd}f}"


def _round_df(df: pd.DataFrame, col_decimals: dict[str, int]) -> pd.DataFrame:
    out = df.copy()
    for col, nd in col_decimals.items():
        if col not in out.columns:
            continue
        s = pd.to_numeric(out[col], errors="coerce")
        if nd == 0:
            out[col] = s.round(0).astype("int64")
        else:
            out[col] = s.round(nd)
    return out


def _net_value_from_pct(pct: pd.Series) -> pd.Series:
    nav = 1.0
    nets: list[float] = []
    for p in pct:
        nav += round(float(p), 8)
        nets.append(round(nav, 4))
    return pd.Series(nets, index=pct.index, dtype="float64")


def _format_nav_table(df: pd.DataFrame, col_map: dict[str, str],
                      col_order: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[col_map[c] for c in col_order])
    out = df.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    if "daily_pnl_pct" in out.columns:
        out["daily_pnl_pct"] = pd.to_numeric(out["daily_pnl_pct"], errors="coerce").round(8)
        out["net_value"] = _net_value_from_pct(out["daily_pnl_pct"])
    dec = {k: DECIMALS[k] for k in ("daily_pnl", "commission") if k in out.columns}
    out = _round_df(out, dec)
    for col in ("num_trades", "win_trades", "num_spreads"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("int64")
    out = out.rename(columns=col_map)
    cols = [col_map[c] for c in col_order if col_map[c] in out.columns]
    return out[cols]


def format_portfolio_nav(df: pd.DataFrame) -> pd.DataFrame:
    return _format_nav_table(df, PORTFOLIO_COL_CN, PORTFOLIO_COL_ORDER)


def format_spread_nav(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[SPREAD_COL_CN[c] for c in SPREAD_COL_ORDER])
    out = df.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"])
    if "daily_pnl_pct" in out.columns:
        out["daily_pnl_pct"] = pd.to_numeric(out["daily_pnl_pct"], errors="coerce").round(8)
        parts: list[pd.DataFrame] = []
        for _, grp in out.groupby("spread", sort=False):
            g = grp.sort_values("date").copy()
            g["net_value"] = _net_value_from_pct(g["daily_pnl_pct"])
            parts.append(g)
        out = pd.concat(parts, ignore_index=True)
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    dec = {k: DECIMALS[k] for k in ("daily_pnl", "commission") if k in out.columns}
    out = _round_df(out, dec)
    for col in ("num_trades", "win_trades"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype("int64")
    out = out.rename(columns=SPREAD_COL_CN)
    cols = [SPREAD_COL_CN[c] for c in SPREAD_COL_ORDER if SPREAD_COL_CN[c] in out.columns]
    return out.sort_values([SPREAD_COL_CN["date"], SPREAD_COL_CN["spread"]])[cols]


def format_trades(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=list(TRADES_COL_CN.values()))
    cols = [c for c in TRADES_COL_CN if c in df.columns]
    out = df[cols].copy()
    out = _round_df(out, {"price": 2, "commission": 2, "volume": 0})
    return out.rename(columns=TRADES_COL_CN)


def format_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=list(SUMMARY_COL_CN.values()))
    dec = {k: DECIMALS[k] for k in df.columns if k in DECIMALS}
    out = _round_df(df, dec)
    out = out.rename(columns=SUMMARY_COL_CN)
    out.index.name = SUMMARY_INDEX_CN
    return out


def _write_nav_csv(df: pd.DataFrame, path: str, formatter) -> None:
    out = formatter(df)
    if out.empty:
        out.to_csv(path, index=False, encoding="utf-8-sig")
        return
    disp = out.copy()
    for col, nd in (("日盈亏", 2), ("日收益率", 8), ("手续费", 2), ("净值", 4)):
        if col in disp.columns:
            disp[col] = disp[col].map(lambda v, n=nd: _fmt_float(v, n))
    for col in ("成交笔数", "盈利笔数", "套利对数"):
        if col in disp.columns:
            disp[col] = disp[col].astype(int)
    disp.to_csv(path, index=False, encoding="utf-8-sig")


def write_portfolio_csv(df: pd.DataFrame, path: str) -> None:
    _write_nav_csv(df, path, format_portfolio_nav)


def write_spread_csv(df: pd.DataFrame, path: str) -> None:
    _write_nav_csv(df, path, format_spread_nav)


def write_trades_csv(df: pd.DataFrame, path: str) -> None:
    out = format_trades(df)
    if out.empty:
        out.to_csv(path, index=False, encoding="utf-8-sig")
        return
    disp = out.copy()
    disp["成交价"] = disp["成交价"].map(lambda v: _fmt_float(v, 2))
    disp["手续费"] = disp["手续费"].map(lambda v: _fmt_float(v, 2))
    disp["成交量"] = disp["成交量"].astype(int)
    disp.to_csv(path, index=False, encoding="utf-8-sig")


def write_summary_csv(df: pd.DataFrame, path: str) -> None:
    out = format_summary(df)
    if out.empty:
        out.to_csv(path, encoding="utf-8-sig")
        return
    disp = out.copy()
    for col, nd in (
        ("累计收益率", 6), ("年化收益率", 6), ("最大回撤", 6),
        ("夏普比率", 4), ("卡玛比率", 4), ("胜率", 4), ("累计手续费", 2),
    ):
        if col in disp.columns:
            disp[col] = disp[col].map(lambda v, n=nd: "" if pd.isna(v) else _fmt_float(v, n))
    for col in ("交易日数", "活跃天数", "成交笔数"):
        if col in disp.columns:
            disp[col] = disp[col].astype(int)
    disp.to_csv(path, encoding="utf-8-sig")
