"""performance/report/loader.py —— 套利回测 DataFrame → 绩效报告输入。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_TRADES_COLS = [
    "trade_id", "trade_date", "symbol", "strategy",
    "direction", "open_time", "close_time",
    "open_price", "close_price", "volume",
    "pnl", "commission", "profit", "pnl_pct",
    "holding_minutes", "close_reason",
]


def load_config(config_path: Path | None = None) -> dict:
    path = config_path or _CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def prepare_frames(
    spread_daily: pd.DataFrame,
    portfolio_daily: pd.DataFrame,
    fills: pd.DataFrame | None,
    capital: float,
    strategy: str = "",
    port_capital: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """将回测内部 daily / fills 转为 CTA 绩效报告所需三表。

    spread_daily: date, spread, daily_pnl, daily_pnl_pct, commission, num_trades, win_trades
    portfolio_daily: date, daily_pnl, daily_pnl_pct, commission, num_trades, win_trades

    capital: 单实例本金（symbol / trades 收益率分母）。
    port_capital: 组合总本金（=活跃实例数×单实例本金），portfolio 毛收益率分母；
        默认与 capital 相同（单实例回测）。
    """
    cap = float(capital)
    port_cap = float(port_capital) if port_capital is not None else cap
    sym = spread_daily.copy()
    sym = sym.rename(columns={"spread": "symbol"})
    sym["date"] = pd.to_datetime(sym["date"]).dt.date
    sym["daily_commission"] = sym["commission"].astype(float)
    sym["gross_daily_pnl_pct"] = (sym["daily_pnl"] + sym["daily_commission"]) / cap
    sym["net_value"] = sym.groupby("symbol")["daily_pnl_pct"].transform(
        lambda s: 1 + s.cumsum()
    )

    port = portfolio_daily.copy()
    port["date"] = pd.to_datetime(port["date"]).dt.date
    port["daily_commission"] = port["commission"].astype(float)
    port["gross_daily_pnl_pct"] = (port["daily_pnl"] + port["daily_commission"]) / port_cap
    port["net_value"] = 1 + port["daily_pnl_pct"].cumsum()

    trades = _fills_to_trades(
        fills if fills is not None and not fills.empty else pd.DataFrame(),
        sym, cap, strategy,
    )
    return port.reset_index(drop=True), sym.reset_index(drop=True), trades


def _fills_to_trades(
    fills: pd.DataFrame,
    spread_daily: pd.DataFrame,
    capital: float,
    strategy: str,
) -> pd.DataFrame:
    if fills.empty:
        return pd.DataFrame(columns=_TRADES_COLS)

    fd = fills.copy()
    fd["trade_date"] = pd.to_datetime(fd["trading_day"]).dt.date
    fd["symbol"] = fd["spread"]
    fd["strategy"] = strategy
    fd["open_time"] = pd.to_datetime(fd["datetime"])
    fd["close_time"] = fd["open_time"]
    fd["open_price"] = fd["price"].astype(float)
    fd["close_price"] = fd["open_price"]
    fd["volume"] = fd["volume"].astype(int)
    fd["commission"] = fd["commission"].astype(float)
    fd["close_reason"] = fd["offset"]
    fd["holding_minutes"] = 0
    fd["trade_id"] = range(len(fd))

    sd = spread_daily.copy()
    sd["date"] = pd.to_datetime(sd["date"]).dt.date
    daily_net = sd.groupby(["date", "symbol"])["daily_pnl"].sum()
    daily_comm = sd.groupby(["date", "symbol"])["daily_commission"].sum()
    fill_cnt = fd.groupby(["trade_date", "symbol"]).size()

    profits: list[float] = []
    pnls: list[float] = []
    pcts: list[float] = []
    for _, row in fd.iterrows():
        key = (row["trade_date"], row["symbol"])
        n = int(fill_cnt.get(key, 1))
        net = float(daily_net.get(key, 0.0)) / n
        gross = net + float(daily_comm.get(key, 0.0)) / n
        profits.append(net)
        pnls.append(gross)
        pcts.append(net / capital if capital > 0 else 0.0)

    fd["profit"] = profits
    fd["pnl"] = pnls
    fd["pnl_pct"] = pcts
    return fd[_TRADES_COLS].sort_values("open_time").reset_index(drop=True)
