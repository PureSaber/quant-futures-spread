"""从主力表提取换月日（带缓存）。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_CACHE: dict[tuple[str, str], set[str]] = {}


def load_roll_dates(dom_dir: Path, product: str, years: list[str]) -> set[str]:
    key = (str(dom_dir), product.upper())
    if key in _CACHE:
        cached = _CACHE[key]
        return {d for d in cached if any(d.startswith(y) for y in years)}

    roll_days: set[str] = set()
    for fp in sorted(dom_dir.glob("主力合约表-20*.csv")):
        df = pd.read_csv(fp)
        df = df[df["product"].astype(str).str.upper() == product.upper()]
        dom = df[df["contract_type"] == "主力"].sort_values("tradingday")
        if dom.empty:
            continue
        prev = None
        for _, row in dom.iterrows():
            c = str(row["contract"])
            td = pd.Timestamp(row["tradingday"]).strftime("%Y-%m-%d")
            if prev is not None and c != prev:
                roll_days.add(td)
            prev = c
    _CACHE[key] = roll_days
    return {d for d in roll_days if any(d.startswith(y) for y in years)}
