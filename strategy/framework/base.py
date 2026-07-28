"""strategy/framework/base.py —— 策略基类与目标挂单结构。

声明式约定：策略 on_bar 返回"目标挂单集"——它希望此刻账面上挂着的**全部**委托快照。
系统(ReconcileEngine) 负责把目标集与该策略实际在挂单 diff，补缺撤多。策略不直接报单/撤单。

side 语义（offset 由系统 OffsetDecider 定，策略只表达买卖开平意图）：
    OPEN_LONG   做多价差/开多   -> 买开
    OPEN_SHORT  做空价差/开空   -> 卖开
    CLOSE_LONG  平多头           -> 卖平
    CLOSE_SHORT 平空头           -> 买平
"""
from __future__ import annotations

from dataclasses import dataclass

OPEN_LONG = "OPEN_LONG"
OPEN_SHORT = "OPEN_SHORT"
CLOSE_LONG = "CLOSE_LONG"
CLOSE_SHORT = "CLOSE_SHORT"

_OPEN_SIDES = {OPEN_LONG, OPEN_SHORT}

# side -> (报单方向 direction, 是否开仓)
SIDE_TO_DIRECTION = {
    OPEN_LONG: ("LONG", True),
    OPEN_SHORT: ("SHORT", True),
    CLOSE_LONG: ("SHORT", False),   # 卖平多头
    CLOSE_SHORT: ("LONG", False),   # 买平空头
}


@dataclass
class TargetOrder:
    """策略声明的一条目标挂单。"""
    symbol: str
    side: str          # OPEN_LONG / OPEN_SHORT / CLOSE_LONG / CLOSE_SHORT
    price: float
    volume: float
    tag: str = ""      # 策略自定义标签（如 "L1-open"），用于日志/自我对账

    @property
    def is_open(self) -> bool:
        return self.side in _OPEN_SIDES

    def to_direction_offset(self) -> tuple[str, bool]:
        """返回 (direction, is_open)。is_open=False 时 offset 由 OffsetDecider 决定。"""
        return SIDE_TO_DIRECTION[self.side]


@dataclass
class DirectSignal:
    """信号式策略的单条即时意图（DirectAdapter 处理，区别于 framework.Signal）。"""
    symbol: str
    side: str
    price: float
    volume: float
    tag: str = ""
    ttl_sec: float = 0.0   # >0 时超时未成交自动撤

    def to_direction_offset(self) -> tuple[str, bool]:
        return SIDE_TO_DIRECTION[self.side]


class Strategy:
    """策略基类。子类按需覆写 on_init/on_bar/on_trade 等。

    约束：
        - 不直接碰 CTP / 不直接报单撤单；只通过返回 TargetOrder 表达意图。
        - 只读账户经由 ctx（行情、历史 K、本策略归属持仓）。
        - on_bar 返回的目标集必须是"完整快照"（不是增量）。
    """

    def __init__(self, strategy_id: str, params: dict, ctx) -> None:
        self.strategy_id = strategy_id
        self.params = dict(params or {})
        self.ctx = ctx
        self.symbol: str = str(self.params.get("symbol") or "").strip()
        # 0=多空都开 / 1=只开多价差 / -1=只开空价差（系统层硬门控开仓方向）
        self.trade_mode: int = int(self.params.get("trade_mode", 0) or 0)

    # 生命周期
    def on_init(self) -> None: ...
    def on_start(self) -> None: ...
    def on_stop(self) -> None: ...

    def on_symbol_switch(self, old_symbol: str, new_symbol: str) -> None:
        """calendar 换月切 spread 时由 runner 调用；子类应重建指标 state。"""
        self.symbol = str(new_symbol or "").strip()
        if self.symbol:
            self.params["symbol"] = self.symbol

    # 行情驱动：返回该策略此刻"完整目标挂单集"。默认空集（不挂任何单）。
    def on_bar(self, bar) -> list[TargetOrder]:
        return []

    # 成交回报：可选返回更新后的完整目标集触发即时 reconcile；返回 None 则不触发。
    # 成交改变了已成交量，策略可借此立即补充新层或更新平仓单，而无需等下一根 K。
    def on_trade(self, trade) -> "list[TargetOrder] | None":
        return None

    # 委托回报（仅通知，不触发 reconcile）
    def on_order(self, order) -> None: ...

    # 系统 sizing 层回调：按 capital_per_layer + 昨收价折出的每层手数，开盘/重连各算一次。
    # 默认写回 params；缓存了手数的子类（如网格）覆盖此方法以同步刷新内部状态。
    def on_sizing(self, vol_per_layer: "list[float]") -> None:
        self.params["vol_per_layer"] = list(vol_per_layer)
