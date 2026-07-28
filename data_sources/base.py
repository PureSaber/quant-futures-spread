"""
data_sources/base.py  ——  价差数据源抽象

统一接口，供引擎按 (品种, 年) 加载滚动 spread 列表与单 spread 的 1 分钟序列。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import pandas as pd


class SpreadBarSource(ABC):
    """价差数据源接口。"""

    @abstractmethod
    def load_symbol_list(self, product: str, years: List[str]) -> pd.DataFrame:
        """返回该品种跨 ``years`` 的滚动 spread 列表（列：symbol/symbol_0/symbol_1/year）。"""

    @abstractmethod
    def load_spread(self, product: str, spread: str, years: List[str]) -> pd.DataFrame:
        """返回单个 spread 跨 ``years`` 的 1 分钟序列（按 datetime 升序，已 reset_index）。"""
