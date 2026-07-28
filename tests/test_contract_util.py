"""tests/test_contract_util.py — future_list 手续费口径。"""
from __future__ import annotations

from pathlib import Path

import pytest

from utils.contract_util import FutureList

FL_PATH = Path(__file__).resolve().parent.parent / "config" / "future_list.csv"


@pytest.fixture
def fl() -> FutureList:
    return FutureList.load(str(FL_PATH))


def test_commission_fixed_independent_of_price(fl: FutureList) -> None:
    assert fl.commission_per_lot("A", 3000.0) == pytest.approx(2.0)
    assert fl.commission_per_lot("A", 8000.0) == pytest.approx(2.0)


def test_commission_proportional_scales_with_price(fl: FutureList) -> None:
    fee = fl.commission_per_lot("AG", 6000.0)
    assert fee == pytest.approx(1e-5 * 6000 * 15)
    assert fl.commission_per_lot("AG", 3000.0) == pytest.approx(fee / 2)


def test_commission_intraday_on_close_today(fl: FutureList) -> None:
    # AP: commission=5, commission_intraday=20
    open_fee = fl.commission_for_spread("AP", "AP", 8000.0, 7900.0, "OPEN")
    close_today = fl.commission_for_spread(
        "AP", "AP", 8100.0, 8000.0, "CLOSE", is_close_today=True,
    )
    close_overnight = fl.commission_for_spread(
        "AP", "AP", 8100.0, 8000.0, "CLOSE", is_close_today=False,
    )
    assert open_fee == pytest.approx(10.0)          # 5+5
    assert close_today == pytest.approx(40.0)       # 20+20
    assert close_overnight == pytest.approx(10.0)   # 5+5


def test_commission_intraday_zero_falls_back(fl: FutureList) -> None:
    # CF: commission=4.3, commission_intraday=0
    fee = fl.commission_for_leg("CF", 15000.0, use_intraday=True)
    assert fee == pytest.approx(4.3)


def test_cost_per_lot_alias(fl: FutureList) -> None:
    assert fl.cost_per_lot("A", 4000.0) == pytest.approx(2.0)
