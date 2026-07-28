"""tests/test_commission.py — 动态手续费与平今跟踪。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.portfolio.commission import (
    SpreadOpenDayTracker, commission_for_trade, resolve_leg_prices,
)
from core.types import BarData
from utils.contract_util import FutureList

FL_PATH = Path(__file__).resolve().parent.parent / "config" / "future_list.csv"


@pytest.fixture
def fl() -> FutureList:
    return FutureList.load(str(FL_PATH))


def _bar(leg_x: float, leg_y: float, td: str = "2020-01-02") -> BarData:
    return BarData(
        symbol="A2003&A2005", exchange="DCE",
        datetime=datetime(2020, 1, 2, 9, 30),
        close_price=leg_x - leg_y,
        trading_day=td,
        leg_close_x=leg_x,
        leg_close_y=leg_y,
    )


def test_resolve_leg_prices_from_bar(fl: FutureList) -> None:
    bar = _bar(4000.0, 3950.0)
    x, y = resolve_leg_prices(bar, fl, "A", "A")
    assert x == 4000.0 and y == 3950.0


def test_proportional_requires_leg_prices(fl: FutureList) -> None:
    bar = BarData(
        symbol="AG2506&AG2508", exchange="SHFE",
        datetime=datetime(2020, 1, 2, 9, 30),
        close_price=10.0, trading_day="2020-01-02",
    )
    with pytest.raises(ValueError, match="close_x/close_y"):
        resolve_leg_prices(bar, fl, "AG", "AG")


def test_open_day_tracker_intraday_close(fl: FutureList) -> None:
    tracker = SpreadOpenDayTracker()
    bar = _bar(8000.0, 7900.0)
    open_comm = commission_for_trade(
        fl, "AP", "AP", bar, "OPEN", 1.0, is_close_today=False,
    )
    tracker.after_trade("s1", "AP2505&AP2510", 0.0, 1.0, "2020-01-02")
    assert tracker.is_close_today("s1", "AP2505&AP2510", "2020-01-02", "CLOSE")
    close_comm = commission_for_trade(
        fl, "AP", "AP", bar, "CLOSE", 1.0,
        is_close_today=tracker.is_close_today("s1", "AP2505&AP2510", "2020-01-02", "CLOSE"),
    )
    assert open_comm == pytest.approx(10.0)
    assert close_comm == pytest.approx(40.0)


def test_overnight_close_uses_normal_rate(fl: FutureList) -> None:
    tracker = SpreadOpenDayTracker()
    tracker.after_trade("s1", "AP2505&AP2510", 0.0, 1.0, "2020-01-01")
    bar = _bar(8000.0, 7900.0, td="2020-01-02")
    assert not tracker.is_close_today("s1", "AP2505&AP2510", "2020-01-02", "CLOSE")
    close_comm = commission_for_trade(
        fl, "AP", "AP", bar, "CLOSE", 1.0,
        is_close_today=tracker.is_close_today("s1", "AP2505&AP2510", "2020-01-02", "CLOSE"),
    )
    assert close_comm == pytest.approx(10.0)
