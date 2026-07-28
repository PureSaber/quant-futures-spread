"""example_cross_product — 跨品种套利（团队示例）。

## 适用场景

不同品种同月合约价差（如豆一&豆二 A2003&B2003）；symbol 由配置手填，不随主力表切换。

## 回测配置要点

```yaml
# config/backtest_example_cross_product.yaml
strategy: example_cross_product
universe:
  mode: manual                    # 手填 symbol，每 spread 一实例
  products: [A, B]                # 腿0/腿1 品种需在 future_list
data:
  source: csv_catalog
  data_dir: .../套利数据-跨品种     # MarketData/{year}/{A&B}/A2003&B2003.csv
```

品种实例在 ``config/strategy_instances/example_cross_product.yaml``：

```yaml
instances:
  - symbol: "A2003&B2003"   # 腿0=A2003 腿1=B2003 → catalog key A&B
```

`utils.contract_util.catalog_key_of("A2003&B2003")` → ``A&B``（跨品种双键目录）。

## 与 dom/sub 示例的区别

| | example_dom_sub | example_cross_product |
|--|-----------------|----------------------|
| universe | calendar_dom_sub | manual |
| symbol 来源 | 主力表 + runner | yaml instances |
| 数据目录 | 套利数据-主力次主力 | 套利数据-跨品种 |
| 换月 | defer_until_flat | 无（固定合约对） |

## 算法

与 dom/sub 示例相同的 z-score 均值回归示意逻辑（有信号才挂单，见 ``example_dom_sub`` 说明）。

**universe.products**：runner 按**腿0品种**过滤实例（``A2003&B2003`` 看 A 是否在列表中）。

params:
    symbol          必填，如 A2003&B2003（manual 由 instances 注入）
    source / lookback / entry_z / trade_mode / capital_per_layer / vol_per_layer
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
        if not self.symbol:
            raise ValueError(f"[{strategy_id}] cross_product 示例须在 params/instances 中指定 symbol")
        self.source = str(self.params.get("source", "comb"))
        self.lookback = int(self.params.get("lookback", 60) or 60)
        self.entry_z = float(self.params.get("entry_z", 1.5) or 1.5)
        self._vol = self._read_vol(self.params.get("vol_per_layer", [1]))

    def _read_vol(self, raw) -> float:
        if isinstance(raw, (int, float)):
            return max(1.0, float(raw))
        vols = [float(v) for v in (raw or [1])]
        return max(1.0, vols[0] if vols else 1.0)

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
                    self.symbol, OPEN_LONG, self._px(last, OPEN_LONG), self._vol, "xprod-z-long",
                ))
            elif z >= self.entry_z and self.trade_mode <= 0:
                targets.append(TargetOrder(
                    self.symbol, OPEN_SHORT, self._px(last, OPEN_SHORT), self._vol, "xprod-z-short",
                ))
        elif pos.long_qty > 1e-9 and z >= 0:
            targets.append(TargetOrder(
                self.symbol, CLOSE_LONG, self._px(last, CLOSE_LONG), pos.long_qty, "xprod-z-exit",
            ))
        elif pos.short_qty > 1e-9 and z <= 0:
            targets.append(TargetOrder(
                self.symbol, CLOSE_SHORT, self._px(last, CLOSE_SHORT), pos.short_qty, "xprod-z-exit",
            ))

        if targets:
            self.ctx.log(
                f"[{self.strategy_id}] bar={bar.datetime} sym={self.symbol} z={z:.3f} "
                f"targets={len(targets)}"
            )
        return targets


__all__ = ["Strategy"]
