"""
data_sources/csv_catalog.py  ——  读 ContractCatalog + MarketData 布局的套利数据

目录布局（跨品种 / 主力次主力共用，仅"品种键"口径不同）：
    {data_dir}/ContractCatalog/{key}_symbol_list.csv          历史 spread 列表（只读存档，不参与 universe 选型）
    {data_dir}/MarketData/{year}/{key}/{symbol_0&symbol_1}.csv 单 spread 1m 序列

key：同品种为 "A"，跨品种为 "A&B"（见 utils.contract_util.catalog_key_of）。
跨品种 catalog 的 symbol 列不可靠（写成 "A2103&A2103"），一律按 symbol_0&symbol_1 重建。
"""
from __future__ import annotations

import glob
import os
from typing import List

import pandas as pd

from .base import SpreadBarSource

_SYMBOL_LIST_SUFFIX = "_symbol_list.csv"


class CsvCatalogSource(SpreadBarSource):
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.catalog_dir = os.path.join(data_dir, "ContractCatalog")
        self.market_dir = os.path.join(data_dir, "MarketData")
        # symbol_list 按 key 缓存（调试/legacy 读盘用；universe 选型不依赖此表）
        self._symbol_list_cache: dict[str, pd.DataFrame] = {}

    # ── 品种键枚举（列举数据目录用）─────────────────────────────────────
    def list_catalog_keys(self) -> List[str]:
        pattern = os.path.join(self.catalog_dir, f"*{_SYMBOL_LIST_SUFFIX}")
        keys = [
            os.path.basename(p)[: -len(_SYMBOL_LIST_SUFFIX)]
            for p in glob.glob(pattern)
        ]
        return sorted(keys)

    # ── symbol_list ──────────────────────────────────────────────────────
    def load_symbol_list(self, key: str, years: List[str]) -> pd.DataFrame:
        # symbol_list 与 years 无关（读整份滚动表），按 key 缓存即可
        cached = self._symbol_list_cache.get(key)
        if cached is not None:
            return cached
        path = os.path.join(self.catalog_dir, f"{key}{_SYMBOL_LIST_SUFFIX}")
        cols = ["symbol", "symbol_0", "symbol_1", "year"]
        if not os.path.exists(path):
            out = pd.DataFrame(columns=cols)
        else:
            df = pd.read_csv(path)
            # 重建 symbol 与文件名一致（跨品种 catalog symbol 列不可信）
            df["symbol"] = df["symbol_0"].astype(str) + "&" + df["symbol_1"].astype(str)
            df = df.drop_duplicates(subset=["symbol"]).sort_values("year").reset_index(drop=True)
            out = df[cols]
        self._symbol_list_cache[key] = out
        return out

    # ── 单 spread 序列 ───────────────────────────────────────────────────
    def load_spread(self, key: str, spread: str, years: List[str]) -> pd.DataFrame:
        frames = []
        for year in years:
            path = os.path.join(self.market_dir, str(year), key, f"{spread}.csv")
            if not os.path.exists(path):
                continue
            frames.append(pd.read_csv(path))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, axis=0)
        df = df.sort_values(["datetime"]).reset_index(drop=True)
        return df
