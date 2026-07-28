"""tests/test_dominant_contract_clean.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.dominant_contract_clean import (
    ProductState,
    contract_yyyymm,
    enforce_time_monotone,
    fix_trio,
)


def test_parse_a2009():
    assert contract_yyyymm("A2009", 2020) == 202009
    assert contract_yyyymm("A2101", 2020) == 202101


def test_fix_a2009_a2005():
    dom, sub, sub3, st = fix_trio("A2009", "A2005", "A2101", 2020)
    assert st == "fixed"
    assert dom == "A2009"
    assert sub == "A2101"
    assert sub3 == "A2105"


def test_fix_already_ok():
    dom, sub, sub3, st = fix_trio("A2005", "A2009", "A2101", 2020)
    assert st == "ok"
    assert sub == "A2009"


def test_fix_a2105_synthesized():
    dom, sub, sub3, st = fix_trio("A2105", "A2101", "A2103", 2020)
    assert st == "fixed"
    assert sub == "A2109"


def test_time_monotone_blocks_dom_rollback():
    """曾出现主力 09 后，不得再出现 05&09，应为 09&11。"""
    st = ProductState(max_dom_k=202009, max_dom="A2009", max_sub_k=202101, max_sub="A2101")
    dom, sub, sub3, tag = enforce_time_monotone("A2005", "A2009", "A2101", st, 2020)
    assert tag == "dom_time_monotone"
    assert dom == "A2009"
    assert contract_yyyymm(sub, 2020) > 202009
    assert contract_yyyymm(sub, 2020) >= 202101


def test_time_monotone_sub_cannot_revert():
    """主力仍为 09 时，次主力不得从 11 退回 05。"""
    st = ProductState(max_dom_k=202009, max_dom="A2009", max_sub_k=202011, max_sub="A2011")
    dom, sub, _, tag = enforce_time_monotone("A2009", "A2005", "", st, 2020)
    assert tag in ("sub_time_monotone", "dom_time_monotone")
    assert dom == "A2009"
    assert contract_yyyymm(sub, 2020) >= 202011


if __name__ == "__main__":
    test_parse_a2009()
    test_fix_a2009_a2005()
    test_fix_already_ok()
    test_fix_a2105_synthesized()
    test_time_monotone_blocks_dom_rollback()
    test_time_monotone_sub_cannot_revert()
    print("OK")
