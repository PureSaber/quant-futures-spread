"""core/portfolio/accounting.py — 价差盯市与日度加性收益。"""
from __future__ import annotations

import pandas as pd


class SpreadAccounting:
    """单 spread 实例：净持仓 × point 盯市，加性日收益。"""

    def __init__(self, point: float, capital: float) -> None:
        self.point = float(point)
        self.capital = float(capital)
        self._prev_close: float | None = None
        self._cum_bar = 0.0
        self._bar_rows: list[dict] = []

    def net_qty(self, long_qty: float, short_qty: float) -> float:
        return float(long_qty) - float(short_qty)

    @staticmethod
    def trade_delta_net(direction: str, offset: str, volume: float) -> float:
        """净持仓变化：开多 +vol，平多 -vol。"""
        direction = (direction or "").strip().upper()
        offset = (offset or "").strip().upper()
        vol = float(volume or 0)
        if vol <= 0:
            return 0.0
        if offset == "OPEN":
            return vol if direction == "LONG" else -vol
        return -vol if direction == "SHORT" else vol

    @staticmethod
    def inbar_pnl(trades, close: float, point: float) -> float:
        """成交 bar 内贡献：Σ Δpos · (close − fill_price) · point。"""
        px = float(close)
        p = float(point)
        total = 0.0
        for tr in trades:
            d = SpreadAccounting.trade_delta_net(tr.direction, tr.offset, tr.volume)
            total += d * (px - float(tr.price)) * p
        return total

    @staticmethod
    def count_win_trades(trades, close: float, point: float) -> int:
        """当日 bar 内 inbar 贡献为正的成交笔数（对齐 CTA win_trades 粒度）。"""
        px = float(close)
        p = float(point)
        n = 0
        for tr in trades:
            d = SpreadAccounting.trade_delta_net(tr.direction, tr.offset, tr.volume)
            if d * (px - float(tr.price)) * p > 0:
                n += 1
        return n

    def mark_bar(self, trading_day: str, close: float, net_start: float,
                 commission: float = 0.0, inbar: float = 0.0,
                 n_trades: int = 0, n_win_trades: int = 0) -> None:
        pnl = 0.0
        if self._prev_close is not None:
            pnl = float(net_start) * (close - self._prev_close) * self.point
        pnl += float(inbar) - float(commission)
        self._cum_bar += pnl
        self._prev_close = close
        self._bar_rows.append({
            "tradingday": trading_day,
            "close": close,
            "net_start": net_start,
            "bar_pnl": pnl,
            "commission": float(commission),
            "n_trades": int(n_trades),
            "n_win_trades": int(n_win_trades),
            "cum_pnl": self._cum_bar,
        })

    _DAILY_COLS = [
        "daily_pnl", "daily_pnl_pct", "commission", "num_trades", "win_trades",
    ]

    def daily_frame(self) -> pd.DataFrame:
        """日度汇总（字段对齐 cta_strategy_analysis symbol daily_pnl）。"""
        if not self._bar_rows:
            return pd.DataFrame(columns=self._DAILY_COLS)
        df = pd.DataFrame(self._bar_rows)
        df["tradingday"] = pd.to_datetime(df["tradingday"])
        g = df.groupby("tradingday")
        daily_cum = g["cum_pnl"].last()
        daily_pnl = daily_cum.diff()
        daily_pnl.iloc[0] = daily_cum.iloc[0]
        out = pd.DataFrame({
            "daily_pnl": daily_pnl,
            "commission": g["commission"].sum(),
            "num_trades": g["n_trades"].sum().astype(int),
            "win_trades": g["n_win_trades"].sum().astype(int),
        })
        out["daily_pnl_pct"] = out["daily_pnl"] / self.capital
        out.index.name = "date"
        return out

    def daily_returns(self) -> pd.Series:
        frame = self.daily_frame()
        if frame.empty:
            return pd.Series(dtype="float64")
        return frame["daily_pnl_pct"]
