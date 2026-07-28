"""data_sources/registry.py  ——  数据源工厂。"""
from __future__ import annotations

from typing import Any, Dict

from .base import SpreadBarSource
from .csv_spread import CsvSpreadSource
from .csv_catalog import CsvCatalogSource


def build_source(source_type: str, params: Dict[str, Any]) -> SpreadBarSource:
    if source_type == "csv_spread":
        return CsvSpreadSource(data_dir=params["data_dir"])
    if source_type == "csv_catalog":
        return CsvCatalogSource(data_dir=params["data_dir"])
    raise ValueError(f"未知数据源类型: {source_type}")
