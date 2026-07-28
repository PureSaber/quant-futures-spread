"""utils/dominant_contract.py — 主力合约表解析（与 clean 工具同口径）。"""
from __future__ import annotations

import re

_CONTRACT_RE = re.compile(r"^([A-Z]+)(\d+)$")

TENOR_LEG_MAP = {
    "dom_sub": ("主力", "次主力"),
    "sub_subsub": ("次主力", "次次主力"),
    "dom_subsub": ("主力", "次次主力"),
}


def contract_yyyymm(contract: str, file_year: int) -> int | None:
    if contract is None or (isinstance(contract, float) and __import__("pandas").isna(contract)):
        return None
    s = str(contract).strip().upper()
    m = _CONTRACT_RE.match(s)
    if not m:
        return None
    digits = m.group(2)
    if len(digits) == 4:
        yy, mm = int(digits[:2]), int(digits[2:])
        yyyy = 2000 + yy if yy < 80 else 1900 + yy
    elif len(digits) == 3:
        decade = (file_year // 10) * 10
        yyyy = decade + int(digits[0])
        mm = int(digits[1:])
    else:
        return None
    if mm < 1 or mm > 12:
        return None
    return yyyy * 100 + mm


def product_of(contract: str) -> str:
    m = _CONTRACT_RE.match(str(contract).strip().upper())
    return m.group(1) if m else ""


def spread_from_legs(leg0: str, leg1: str) -> str:
    return f"{str(leg0).strip()}&{str(leg1).strip()}"


def normalize_tenor(tenor: str, *, for_mode: str = "") -> str:
    t = str(tenor or "dom_sub").strip().lower()
    if for_mode == "calendar_dom_sub":
        return "dom_sub"
    if t not in TENOR_LEG_MAP:
        raise ValueError(f"未知 tenor: {tenor!r}，可选 {list(TENOR_LEG_MAP)}")
    return t
