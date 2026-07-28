"""core/types.py — BarData / TradeData。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BarData:
    symbol: str
    exchange: str
    datetime: datetime
    interval: str = "1m"
    product_id: str = ""
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    close_price: float = 0.0
    volume: float = 0.0
    open_interest: float = 0.0
    trading_day: str = ""
    action_day: str = ""
    source: str = "comb"
    is_completed: bool = True
    tradable: bool = True
    # spread 盘口 OHLC（撮合用；缺省回退 comb high/low）
    bid_high: float = 0.0
    bid_low: float = 0.0
    ask_high: float = 0.0
    ask_low: float = 0.0
    # 点价盘口与预计算分位（与 research CSV 对齐）
    bid_price: float = 0.0
    ask_price: float = 0.0
    close_20: float = 0.0
    close_50: float = 0.0
    close_80: float = 0.0
    bid_price_20: float = 0.0
    bid_price_50: float = 0.0
    bid_price_80: float = 0.0
    ask_price_20: float = 0.0
    ask_price_50: float = 0.0
    ask_price_80: float = 0.0
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    # 单腿收盘价（手续费 type=1 按当 bar 腿价计；0 表示缺失）
    leg_close_x: float = 0.0
    leg_close_y: float = 0.0


@dataclass
class TradeData:
    strategy_id: str
    symbol: str
    exchange: str
    order_id: str
    trade_id: str
    direction: str
    offset: str
    price: float
    volume: float
    datetime: datetime = field(default_factory=datetime.now)
    trading_day: str = ""
