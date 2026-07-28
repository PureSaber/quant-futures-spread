"""factor_single — Round1 Top5 单因子策略（每实例仅一个因子）。"""
from __future__ import annotations

from utils import ta
from framework.base import (
    CLOSE_LONG,
    CLOSE_SHORT,
    OPEN_LONG,
    OPEN_SHORT,
    Strategy as BaseStrategy,
    TargetOrder,
)

from strategies.factor_single.factors import (
    compute_entry_rank,
    compute_factor_value,
    factor_ic_sign,
    factor_uses_pct_band,
    factor_uses_quantile_entry,
)


class Strategy(BaseStrategy):
    """params:
        factor, horizon_min, lookback, entry_z, entry_low, entry_high, exit_mid
        min_hold_bars, cooldown_bars, max_eff_spread, use_conservative_fill
    """

    def __init__(self, strategy_id: str, params: dict, ctx) -> None:
        super().__init__(strategy_id, params, ctx)
        self.factor = str(self.params.get("factor", "pct_rank")).strip().lower()
        self.horizon_min = int(self.params.get("horizon_min", 15) or 15)
        self.lookback = int(self.params.get("lookback", 60) or 60)
        self.entry_z = float(self.params.get("entry_z", 1.5) or 1.5)
        self.entry_low = float(self.params.get("entry_low", 0.2) or 0.2)
        self.entry_high = float(self.params.get("entry_high", 0.8) or 0.8)
        self.exit_mid = float(self.params.get("exit_mid", 0.5) or 0.5)
        self.min_hold_bars = int(self.params.get("min_hold_bars", 5) or 5)
        self.cooldown_bars = int(self.params.get("cooldown_bars", 3) or 3)
        self.max_eff_spread = float(self.params.get("max_eff_spread", 20) or 20)
        self.use_conservative_fill = bool(self.params.get("use_conservative_fill", False))
        self.source = str(self.params.get("source", "comb"))
        self._ic_sign = int(self.params.get("ic_sign", factor_ic_sign(self.factor)))
        self._vol = self._read_vol(self.params.get("vol_per_layer", [1]))
        self._bars_in_pos = 0
        self._cooldown_left = 0

    def _read_vol(self, raw) -> float:
        if isinstance(raw, (int, float)):
            return max(1.0, float(raw))
        vols = [float(v) for v in (raw or [1])]
        return max(1.0, vols[0] if vols else 1.0)

    def on_symbol_switch(self, old_symbol: str, new_symbol: str) -> None:
        super().on_symbol_switch(old_symbol, new_symbol)
        self._bars_in_pos = 0
        self._cooldown_left = 0

    def on_sizing(self, vol_per_layer: list[float]) -> None:
        super().on_sizing(vol_per_layer)
        if vol_per_layer:
            self._vol = self._read_vol(vol_per_layer)

    def _tick(self) -> float:
        t = self.ctx.price_tick_of(self.symbol)
        return t if t > 0 else 0.01

    def _px(self, price: float, side: str) -> float:
        tick = self._tick()
        if side in (OPEN_LONG, CLOSE_SHORT):
            return ta.round_down(price, tick)
        return ta.round_up(price, tick)

    def _eff_spread(self, bar) -> float:
        ask = float(getattr(bar, "ask_price", 0) or getattr(bar, "ask_high", 0) or bar.close_price)
        bid = float(getattr(bar, "bid_price", 0) or getattr(bar, "bid_low", 0) or bar.close_price)
        return ask - bid

    def _order_price(self, bar, side: str) -> float:
        if not self.use_conservative_fill:
            return float(bar.close_price)
        ask = float(getattr(bar, "ask_price", 0) or getattr(bar, "ask_high", 0) or bar.close_price)
        bid = float(getattr(bar, "bid_price", 0) or getattr(bar, "bid_low", 0) or bar.close_price)
        if side in (OPEN_LONG, CLOSE_SHORT):
            return ask
        return bid

    def _signal_val(self, bars, bar) -> float | None:
        if factor_uses_quantile_entry(self.factor):
            return compute_entry_rank(self.factor, bars, bar, self.symbol, self.lookback)
        return compute_factor_value(self.factor, bars, bar, self.symbol, self.lookback)

    def _uses_band_entry(self) -> bool:
        return (
            self.factor in ("pct_rank",)
            or factor_uses_quantile_entry(self.factor)
            or factor_uses_pct_band(self.factor)
        )

    def _want_long(self, val: float) -> bool:
        if self._uses_band_entry():
            if self._ic_sign > 0:
                return val >= self.entry_high
            return val <= self.entry_low
        if self.factor == "breakout_down_60":
            return val >= 0.5 and self._ic_sign > 0
        if self.factor == "breakout_up_60":
            return False
        return val <= -self.entry_z

    def _want_short(self, val: float) -> bool:
        if self._uses_band_entry():
            if self._ic_sign > 0:
                return val <= self.entry_low
            return val >= self.entry_high
        if self.factor == "breakout_up_60":
            return val >= 0.5
        if self.factor == "breakout_down_60":
            return False
        return val >= self.entry_z

    def _want_exit_long(self, val: float) -> bool:
        if self._bars_in_pos < self.min_hold_bars:
            return False
        if self._bars_in_pos >= self.horizon_min:
            return True
        if self._uses_band_entry():
            if self._ic_sign > 0:
                return val <= self.exit_mid
            return val >= self.exit_mid
        if self.factor == "breakout_down_60":
            return val < 0.5
        if self.factor == "breakout_up_60":
            return val < 0.5
        return val >= 0

    def _want_exit_short(self, val: float) -> bool:
        if self._bars_in_pos < self.min_hold_bars:
            return False
        if self._bars_in_pos >= self.horizon_min:
            return True
        if self._uses_band_entry():
            if self._ic_sign > 0:
                return val >= self.exit_mid
            return val <= self.exit_mid
        return val <= 0

    def on_bar(self, bar) -> list[TargetOrder]:
        if not self.symbol:
            return []

        limit = max(self.lookback + 65, 120)
        bars = self.ctx.get_bars(self.symbol, source=self.source, limit=limit)
        val = self._signal_val(bars, bar)
        if val is None:
            return []

        pos = self.ctx.get_position(self.symbol)
        targets: list[TargetOrder] = []
        tag = f"f-{self.factor}"

        in_pos = pos.long_qty > 1e-9 or pos.short_qty > 1e-9
        if in_pos:
            self._bars_in_pos += 1
        else:
            self._bars_in_pos = 0
            if self._cooldown_left > 0:
                self._cooldown_left -= 1

        if pos.long_qty <= 1e-9 and pos.short_qty <= 1e-9:
            if self._cooldown_left > 0:
                return []
            if self._eff_spread(bar) > self.max_eff_spread:
                return []
            if self._want_long(val) and self.trade_mode >= 0:
                px = self._px(self._order_price(bar, OPEN_LONG), OPEN_LONG)
                targets.append(TargetOrder(self.symbol, OPEN_LONG, px, self._vol, f"{tag}-long"))
                self._bars_in_pos = 0
            elif self._want_short(val) and self.trade_mode <= 0:
                px = self._px(self._order_price(bar, OPEN_SHORT), OPEN_SHORT)
                targets.append(TargetOrder(self.symbol, OPEN_SHORT, px, self._vol, f"{tag}-short"))
                self._bars_in_pos = 0
        elif pos.long_qty > 1e-9 and self._want_exit_long(val):
            px = self._px(self._order_price(bar, CLOSE_LONG), CLOSE_LONG)
            targets.append(TargetOrder(self.symbol, CLOSE_LONG, px, pos.long_qty, f"{tag}-exit"))
            self._bars_in_pos = 0
            self._cooldown_left = self.cooldown_bars
        elif pos.short_qty > 1e-9 and self._want_exit_short(val):
            px = self._px(self._order_price(bar, CLOSE_SHORT), CLOSE_SHORT)
            targets.append(TargetOrder(self.symbol, CLOSE_SHORT, px, pos.short_qty, f"{tag}-exit"))
            self._bars_in_pos = 0
            self._cooldown_left = self.cooldown_bars

        return targets


__all__ = ["Strategy"]
