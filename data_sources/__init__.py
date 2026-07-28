"""data_sources  ——  可插拔价差数据源。"""
from __future__ import annotations

from .base import SpreadBarSource
from .csv_spread import CsvSpreadSource
from .csv_catalog import CsvCatalogSource
from .registry import build_source

__all__ = ["SpreadBarSource", "CsvSpreadSource", "CsvCatalogSource", "build_source"]
