"""example_dom_sub — 主力×次主力日历套利（团队示例）。

## 适用场景

同品种主力对次主力（dom/sub）价差；symbol 随交易日历自动切换，持仓可跨换月延续。

## 回测配置要点

```yaml
# config/backtest_example_dom_sub.yaml
strategy: example_dom_sub
universe:
  mode: calendar_dom_sub          # 按主力表选 spread，每品种一连续实例
  products: [A]
  dom_table_dir: .../商品期货-主力合约
data:
  source: csv_catalog
  data_dir: .../套利数据-主力次主力   # MarketData/{year}/{A}/A2105&A2109.csv
roll:
  on_switch: defer_until_flat       # 有仓时先平旧 spread 再切新 spread
```

`strategies.yaml` 里**不要**写 `symbol`，只写品种级 `params`；runner 注入 `product`，首日写入 `symbol`。

## 策略侧约定

1. 交易对象始终是 ``self.symbol``（形如 ``A2105&A2109``）。
2. 换 spread 时 runner 调用 ``on_symbol_switch``，须重置指标/状态。
3. ``on_bar`` 返回完整 ``TargetOrder`` 快照；不引用 core 层模块。

## 算法（示意）

价差收盘价相对 lookback 均值的 z-score：过低做多价差、回归均值平仓。
仅作教学，非实盘推荐逻辑。

**挂单模式**：本示例为「有信号才返回 TargetOrder」；无信号时返回空列表（撤掉该 spread 挂单）。
与 ``boll_grid`` 的「每根 K 全量网格快照」不同，但符合框架声明式 reconcile 约定。

**on_symbol_switch**：本策略指标每 bar 从 ``ctx.get_bars`` 重算，无本地缓存，覆写里打日志即可；
若团队策略有 RSI/EMA 等状态，须在换 spread 时清零或重建。

params:
    source          K 线来源，默认 comb
    lookback        均值窗口，默认 60
    entry_z         开仓 |z| 阈值，默认 1.5
    trade_mode      0 双向 / 1 只做多价差 / -1 只做空价差
    capital_per_layer  资金分层（runner 折手数）
    vol_per_layer      或直接指定手数
"""
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


def _closes(ctx, symbol: str, source: str, limit: int) -> list[float]:
    return [
        float(b["close_price"])
        for b in ctx.get_bars(symbol, source=source, limit=limit)
        if b.get("close_price") is not None
    ]


def _zscore(closes: list[float], lookback: int) -> float | None:
    if len(closes) < lookback:
        return None
    window = closes[-lookback:]
    mu = sum(window) / len(window)
    var = sum((x - mu) ** 2 for x in window) / len(window)
    std = var ** 0.5
    if std < 1e-12:
        return 0.0
    return (closes[-1] - mu) / std


class Strategy(BaseStrategy):
    def __init__(self, strategy_id: str, params: dict, ctx) -> None:
        super().__init__(strategy_id, params, ctx)
        self.source = str(self.params.get("source", "comb"))
        self.lookback = int(self.params.get("lookback", 60) or 60)
        self.entry_z = float(self.params.get("entry_z", 1.5) or 1.5)
        self._vol = self._read_vol(self.params.get("vol_per_layer", [1]))

    def _read_vol(self, raw) -> float:
        if isinstance(raw, (int, float)):
            return max(1.0, float(raw))
        vols = [float(v) for v in (raw or [1])]
        return max(1.0, vols[0] if vols else 1.0)

    def on_init(self) -> None:
        prod = str(self.params.get("product") or "")
        self.ctx.log(
            f"[{self.strategy_id}] init product={prod} symbol={self.symbol or '(runner 注入)'}"
        )

    def on_symbol_switch(self, old_symbol: str, new_symbol: str) -> None:
        super().on_symbol_switch(old_symbol, new_symbol)
        self.ctx.log(
            f"[{self.strategy_id}] 换月切 spread: {old_symbol} -> {new_symbol} "
            f"(product={self.params.get('product', '')})"
        )

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

    def on_bar(self, bar) -> list[TargetOrder]:
        if not self.symbol:
            return []
        closes = _closes(self.ctx, self.symbol, self.source, self.lookback + 2)
        z = _zscore(closes, self.lookback)
        if z is None:
            return []

        pos = self.ctx.get_position(self.symbol)
        last = float(bar.close_price)
        targets: list[TargetOrder] = []

        if pos.long_qty <= 1e-9 and pos.short_qty <= 1e-9:
            if z <= -self.entry_z and self.trade_mode >= 0:
                targets.append(TargetOrder(
                    self.symbol, OPEN_LONG, self._px(last, OPEN_LONG), self._vol, "dom-z-long",
                ))
            elif z >= self.entry_z and self.trade_mode <= 0:
                targets.append(TargetOrder(
                    self.symbol, OPEN_SHORT, self._px(last, OPEN_SHORT), self._vol, "dom-z-short",
                ))
        elif pos.long_qty > 1e-9 and z >= 0:
            targets.append(TargetOrder(
                self.symbol, CLOSE_LONG, self._px(last, CLOSE_LONG), pos.long_qty, "dom-z-exit",
            ))
        elif pos.short_qty > 1e-9 and z <= 0:
            targets.append(TargetOrder(
                self.symbol, CLOSE_SHORT, self._px(last, CLOSE_SHORT), pos.short_qty, "dom-z-exit",
            ))

        if targets:
            self.ctx.log(
                f"[{self.strategy_id}] bar={bar.datetime} sym={self.symbol} z={z:.3f} "
                f"targets={len(targets)}"
            )
        return targets


__all__ = ["Strategy"]
