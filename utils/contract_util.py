"""utils/contract_util.py —— 合约 / 品种工具（future_list、价差拆腿）。"""
from __future__ import annotations

import math
import re
from typing import Tuple

import pandas as pd


def split_spread(spread: str) -> Tuple[str, str]:
    """"A2003&A2005" -> ("A2003", "A2005")。"""
    near, far = spread.split("&")
    return near, far


def product_of(contract: str) -> str:
    """"A2003" -> "A"；取字母部分并大写。"""
    return "".join(re.findall(r"[A-Za-z]", contract)).upper()


def catalog_key_of(spread: str) -> str:
    """价差 → 数据集"品种键"（ContractCatalog / MarketData 子目录名）。

    同品种（跨期 / 主力次主力）-> 单品种 "A"；跨品种 -> "A&B"。
    """
    near, far = split_spread(spread)
    p0, p1 = product_of(near), product_of(far)
    return p0 if p0 == p1 else f"{p0}&{p1}"


def _valid_rate(v) -> bool:
    if v is None or v == "":
        return False
    try:
        f = float(v)
        return math.isfinite(f) and f != 0.0
    except (TypeError, ValueError):
        return False


def _is_fixed_fee(row: pd.Series, rate: float) -> bool:
    """type=0 定额；type=1 比例；缺 type 时 rate>=1 视为定额。"""
    raw_type = row.get("type")
    try:
        t = float(raw_type)
        if t == 0.0:
            return True
        if t == 1.0:
            return False
    except (TypeError, ValueError):
        pass
    return rate >= 1.0


class FutureList:
    """品种参数表封装（index = product）。"""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    @classmethod
    def load(cls, path: str, encoding: str = "utf-8") -> "FutureList":
        try:
            df = pd.read_csv(path, index_col=0, encoding=encoding)
        except UnicodeDecodeError:
            df = pd.read_csv(path, index_col=0, encoding="GBK")
        df.index = df.index.astype(str)
        return cls(df)

    def get(self, product: str, field: str) -> float:
        return self._df.loc[product, field]

    def products(self) -> list:
        return [str(i) for i in self._df.index.tolist()]

    def is_tradable(self, product: str) -> bool:
        return str(self._df.loc[product, "trade"]).upper() == "TRUE"

    def tick_size(self, product: str) -> float:
        return float(self._df.loc[product, "tick_size"])

    def point(self, product: str) -> float:
        return float(self._df.loc[product, "point"])

    def margin_rate(self, product: str) -> float:
        return float(self._df.loc[product, "margin_rate"])

    def is_proportional(self, product: str) -> bool:
        """是否按成交额比例计费（type=1）。"""
        row = self._df.loc[product]
        rate = float(row["commission"]) if _valid_rate(row.get("commission")) else 1.0
        return not _is_fixed_fee(row, rate)

    def commission_per_lot(self, product: str, market: "pd.Series | float") -> "pd.Series | float":
        """单腿单手手续费（开仓/平昨口径，不含滑点）。

        type==1：commission·market·point
        type==0：commission（定额，元/手/边）
        """
        return self.commission_for_leg(product, market, use_intraday=False)

    def commission_for_leg(
        self, product: str, price: "pd.Series | float", *, use_intraday: bool,
    ) -> "pd.Series | float":
        """单腿单手手续费；平今时 use_intraday=True 优先 commission_intraday。"""
        row = self._df.loc[product]
        normal = row.get("commission")
        intraday = row.get("commission_intraday")
        if use_intraday:
            raw = intraday if _valid_rate(intraday) else normal
        else:
            raw = normal if _valid_rate(normal) else intraday
        if not _valid_rate(raw):
            rate = 5.0
        else:
            rate = float(raw)
        if _is_fixed_fee(row, rate):
            return rate
        return rate * price * float(row["point"])

    def commission_for_spread(
        self,
        leg0_product: str,
        leg1_product: str,
        leg0_price: float,
        leg1_price: float,
        offset: str,
        *,
        is_close_today: bool = False,
    ) -> float:
        """价差 1 手成交的手续费 = 两腿各 1 手单边费之和。

        开仓 / 平昨：commission；平今：commission_intraday（缺则回退）。
        """
        off = (offset or "").strip().upper()
        use_intraday = off == "CLOSE" and is_close_today
        c0 = self.commission_for_leg(leg0_product, leg0_price, use_intraday=use_intraday)
        c1 = self.commission_for_leg(leg1_product, leg1_price, use_intraday=use_intraday)
        return float(c0) + float(c1)

    def cost_per_lot(self, product: str, market: "pd.Series | float") -> "pd.Series | float":
        """兼容别名 → :meth:`commission_per_lot`。"""
        return self.commission_per_lot(product, market)


__all__ = ["split_spread", "product_of", "catalog_key_of", "FutureList"]
