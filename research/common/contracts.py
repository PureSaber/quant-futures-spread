"""合约月份解析（因子：carry、roll）。"""
from __future__ import annotations

import re
from datetime import date

_CONTRACT_RE = re.compile(r"^([A-Z]+)(\d+)$")


def product_of(contract: str) -> str:
    m = _CONTRACT_RE.match(str(contract).strip().upper())
    return m.group(1) if m else ""


def contract_yyyymm(contract: str, file_year: int) -> int | None:
    if not contract:
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


def spread_months(spread: str, file_year: int) -> tuple[int | None, int | None]:
    parts = str(spread).split("&")
    if len(parts) != 2:
        return None, None
    return contract_yyyymm(parts[0], file_year), contract_yyyymm(parts[1], file_year)


def days_between_contracts(near_yyyymm: int, far_yyyymm: int) -> int | None:
    if near_yyyymm is None or far_yyyymm is None or far_yyyymm <= near_yyyymm:
        return None
    ny, nm = divmod(near_yyyymm, 100)
    fy, fm = divmod(far_yyyymm, 100)
    try:
        d0 = date(ny, nm, 15)
        d1 = date(fy, fm, 15)
    except ValueError:
        return None
    return max((d1 - d0).days, 1)


def parse_spread_id(spread: str) -> tuple[str, str, str]:
    """返回 (leg0, leg1, pair_type) pair_type=calendar|cross."""
    leg0, leg1 = spread.split("&")
    p0, p1 = product_of(leg0), product_of(leg1)
    ptype = "calendar" if p0 == p1 else "cross"
    return leg0, leg1, ptype
