"""tools/verify_accounting_identity.py —— 记账引擎独立对账（不依赖外部数据）。

用真实组件（ReconcileSimulator + BacktestPositionBook + SpreadAccounting），
按 runner 的 bar 循环驱动一个受控开平场景，验证三条核心恒等式：

  1. 无滑点：成交价 == 限价（TargetOrder.price）。
  2. 当日收盘回到空仓时：accounting 日盈亏 == 由成交逐笔重构的已实现盈亏 − 手续费。
  3. 跨 bar 持仓的盯市：日盈亏 == Σ_bar net_start×Δclose×point + inbar − 手续费。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.strategy_bootstrap import bootstrap_strategy_path  # noqa: E402

bootstrap_strategy_path()

from framework.base import TargetOrder, OPEN_LONG, CLOSE_LONG  # noqa: E402
from core.portfolio.accounting import SpreadAccounting  # noqa: E402
from core.portfolio.position_book import BacktestPositionBook  # noqa: E402
from core.engine.reconcile_sim import ReconcileSimulator  # noqa: E402
from core.types import BarData  # noqa: E402

SYM = "A2003&A2005"
POINT = 10.0
COST = 8.0
CAP = 1_000_000.0


def _bar(i: int, o, h, lo, c) -> BarData:
    return BarData(
        symbol=SYM, exchange="DCE",
        datetime=datetime(2020, 1, 2, 9, i),
        open_price=o, high_price=h, low_price=lo, close_price=c,
        source="comb", trading_day="2020-01-02", volume=1.0,
    )


def run_scenario(targets_by_bar, bars):
    """完整复刻 runner 的单实例 bar 循环（撮合→记账）。返回 (fills, acct)。"""
    book = BacktestPositionBook()
    sim = ReconcileSimulator()
    acct = SpreadAccounting(POINT, CAP)
    fills: list = []
    for i, bar in enumerate(bars):
        sim.reset_opens_if_flat("s1", SYM, book)
        targets = targets_by_bar.get(i, [])
        sim.reconcile("s1", SYM, targets, trade_mode=0)
        pos = book.get_strategy_position("s1", SYM)
        net_start = acct.net_qty(pos.long_qty, pos.short_qty)
        trades = sim.try_fill(bar, book)
        commission = 0.0
        for tr in trades:
            commission += COST * tr.volume
            book.apply_trade(tr.strategy_id, tr.symbol, tr.direction,
                             tr.offset, tr.volume, tr.price)
            fills.append(tr)
        inbar = SpreadAccounting.inbar_pnl(trades, bar.close_price, POINT)
        acct.mark_bar(bar.trading_day, bar.close_price, net_start,
                      commission, inbar, n_trades=len(trades))
    return fills, acct


def realized_from_fills(fills) -> float:
    """flat→flat 全程已实现盈亏 = −Σ Δnet × price × point。"""
    total = 0.0
    for tr in fills:
        d = SpreadAccounting.trade_delta_net(tr.direction, tr.offset, tr.volume)
        total += -d * float(tr.price) * POINT
    return total


def main() -> None:
    errors = []

    def check(cond, label, detail=""):
        mark = "[ok  ]" if cond else "[FAIL]"
        print(f"  {mark} {label}" + ("" if cond else f"  ->  {detail}"))
        if not cond:
            errors.append(label)

    # ── 场景 A：当日开多→平多，收盘回到空仓 ──────────────────────
    print("\n=== 场景 A：日内开平（flat→flat），trades 完整解释日盈亏 ===")
    bars = [
        _bar(1, 100, 100, 100, 100),   # flat
        _bar(2, 98, 101, 97, 100),     # 开多 1@98（low 97<=98 触达）
        _bar(3, 100, 103, 100, 102),   # 持多盯市
        _bar(4, 101, 104, 101, 103),   # 平多 1@103（high 104>=103）
        _bar(5, 103, 103, 103, 103),   # flat 收盘
    ]
    targets = {
        1: [TargetOrder(SYM, OPEN_LONG, 98.0, 1, "L0")],
        2: [TargetOrder(SYM, OPEN_LONG, 98.0, 1, "L0")],
        3: [TargetOrder(SYM, CLOSE_LONG, 103.0, 1, "TP")],
    }
    fills, acct = run_scenario(targets, bars)

    check(len(fills) == 2, "成交 2 笔（开1+平1）", f"got {len(fills)}")
    open_fill = next(f for f in fills if f.offset == "OPEN")
    close_fill = next(f for f in fills if f.offset == "CLOSE")
    check(float(open_fill.price) == 98.0, "开仓成交价==限价 98（无滑点）", str(open_fill.price))
    check(float(close_fill.price) == 103.0, "平仓成交价==限价 103（无滑点）", str(close_fill.price))

    daily = acct.daily_frame()
    daily_pnl = float(daily["daily_pnl"].iloc[0])
    commission = float(daily["commission"].iloc[0])
    realized = realized_from_fills(fills)   # (103-98)*10 = 50
    check(abs(realized - 50.0) < 1e-9, "逐笔重构已实现盈亏 = (103-98)*10 = 50", str(realized))
    check(abs(daily_pnl - (realized - commission)) < 1e-9,
          "记账日盈亏 == 已实现盈亏 − 手续费", f"{daily_pnl} vs {realized-commission}")
    check(abs(commission - 16.0) < 1e-9, "手续费 = 2笔×8 = 16", str(commission))
    check(abs(float(daily["daily_pnl_pct"].iloc[0]) - daily_pnl / CAP) < 1e-15,
          "日收益率 = 日盈亏 / capital", "")

    # ── 场景 B：跨 bar 持仓盯市的逐 bar 分解 ─────────────────────
    print("\n=== 场景 B：逐 bar 盯市分解 == 日盈亏 ===")
    # 复用场景 A 的 acct，校验 bar_pnl 公式
    rows = acct._bar_rows
    recomputed = 0.0
    prev_close = None
    for r in rows:
        ns = r["net_start"]
        carry = 0.0 if prev_close is None else ns * (r["close"] - prev_close) * POINT
        recomputed += carry
        prev_close = r["close"]
    # inbar 与 commission 累加
    recomputed += sum(
        SpreadAccounting.inbar_pnl([f], cl, POINT)
        for f, cl in [(open_fill, 100.0), (close_fill, 103.0)]
    )
    recomputed -= commission
    check(abs(recomputed - daily_pnl) < 1e-9,
          "Σ_bar(net_start×Δclose×point) + Σinbar − 手续费 == 日盈亏",
          f"{recomputed} vs {daily_pnl}")

    # ── 场景 C：净值加性 ────────────────────────────────────────
    print("\n=== 场景 C：净值加性（多日累加）===")
    acct2 = SpreadAccounting(POINT, CAP)
    acct2.mark_bar("2020-01-02", 100.0, 0.0)
    acct2.mark_bar("2020-01-02", 110.0, 1.0)          # +100
    acct2.mark_bar("2020-01-03", 105.0, 1.0)          # −50 次日
    df = acct2.daily_frame()
    nav = 1.0 + df["daily_pnl_pct"].cumsum()
    check(abs(df["daily_pnl"].iloc[0] - 100.0) < 1e-9, "首日盈亏=+100", str(df["daily_pnl"].iloc[0]))
    check(abs(df["daily_pnl"].iloc[1] - (-50.0)) < 1e-9, "次日盈亏=−50", str(df["daily_pnl"].iloc[1]))
    check(abs(float(nav.iloc[-1]) - (1 + 50.0 / CAP)) < 1e-12,
          "净值 = 1 + Σ日收益率（加性，非复利）", str(float(nav.iloc[-1])))

    print("\n" + "=" * 56)
    if errors:
        print(f"[FAIL] {len(errors)} 项不通过: {errors}")
        sys.exit(1)
    print("[ok  ] 记账引擎恒等式全部通过")


if __name__ == "__main__":
    main()
