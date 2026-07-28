"""core/data/dominant_calendar.py — 加载清洗后主力合约表。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils.dominant_contract import TENOR_LEG_MAP, normalize_tenor, spread_from_legs


class DominantCalendar:
    """product × tradingday → 主力/次主力/次次主力 contract。"""

    def __init__(self, table_dir: str | Path) -> None:
        self.table_dir = Path(table_dir)
        self._by_product_day: dict[str, dict[str, dict[str, str]]] = {}
        self._products: set[str] = set()
        self._load()

    def _load(self) -> None:
        files = sorted(self.table_dir.glob("主力合约表-20*.csv"))
        if not files:
            raise FileNotFoundError(f"未找到主力合约表: {self.table_dir}")
        frames = [pd.read_csv(fp) for fp in files]
        df = pd.concat(frames, ignore_index=True)
        for (td, prod), g in df.groupby(["tradingday", "product"]):
            prod = str(prod).upper()
            piv = {
                str(k): str(v).strip()
                for k, v in g.set_index("contract_type")["contract"].items()
                if v == v and str(v).strip()
            }
            self._by_product_day.setdefault(prod, {})[str(td)] = piv
            self._products.add(prod)

    @property
    def products(self) -> list[str]:
        return sorted(self._products)

    def legs(self, product: str, tradingday: str) -> dict[str, str] | None:
        return self._by_product_day.get(str(product).upper(), {}).get(str(tradingday))

    def spread_of(self, product: str, tradingday: str, tenor: str = "dom_sub") -> str | None:
        t = normalize_tenor(tenor)
        leg0_role, leg1_role = TENOR_LEG_MAP[t]
        piv = self.legs(product, tradingday)
        if not piv:
            return None
        leg0 = piv.get(leg0_role, "")
        leg1 = piv.get(leg1_role, "")
        if not leg0 or not leg1:
            return None
        return spread_from_legs(leg0, leg1)

    def trading_days(self, product: str, years: list[str] | None = None) -> list[str]:
        days = sorted(self._by_product_day.get(str(product).upper(), {}).keys())
        if not years:
            return days
        ys = {str(y) for y in years}
        return [d for d in days if d[:4] in ys]
