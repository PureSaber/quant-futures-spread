"""
data_sources/csv_spread.py — 读「全品种跨期合约套利数据」CSV。

目录：{data_dir}/{year}/{product}_symbol_list.csv
      {data_dir}/{year}/{product}/{product}/{spread}.csv
"""
from __future__ import annotations

import os
from typing import List

import pandas as pd

from .base import SpreadBarSource


class CsvSpreadSource(SpreadBarSource):
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    # ── symbol_list ──────────────────────────────────────────────────────

    def load_symbol_list(self, product: str, years: List[str]) -> pd.DataFrame:
        frames = []
        for year in years:
            path = os.path.join(self.data_dir, str(year), f"{product}_symbol_list.csv")
            if not os.path.exists(path):
                continue
            frames.append(pd.read_csv(path))
        if not frames:
            return pd.DataFrame(columns=["symbol", "symbol_0", "symbol_1", "year"])
        df = pd.concat(frames, axis=0)
        df = df.drop_duplicates().sort_values("year").reset_index(drop=True)
        return df

    # ── 单 spread 序列 ───────────────────────────────────────────────────

    def _spread_path(self, year: str, product: str, spread: str) -> str:
        # 优先双层 product
        p2 = os.path.join(self.data_dir, str(year), product, product, f"{spread}.csv")
        if os.path.exists(p2):
            return p2
        # 回退单层
        return os.path.join(self.data_dir, str(year), product, f"{spread}.csv")

    def load_spread(self, product: str, spread: str, years: List[str]) -> pd.DataFrame:
        frames = []
        for year in years:
            path = self._spread_path(str(year), product, spread)
            if not os.path.exists(path):
                continue
            frames.append(pd.read_csv(path))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, axis=0)
        df = df.sort_values(["datetime"]).reset_index(drop=True)
        return df
