"""Shared data path resolution for futures research."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_ROOT = Path("D:/data")


def data_root() -> Path:
    raw = os.environ.get("QUANT_FUTURES_DATA_ROOT", "").strip()
    return Path(raw) if raw else DEFAULT_DATA_ROOT


def cross_dir() -> Path:
    return data_root() / "跨品种"


def dom_table_dir() -> Path:
    return data_root() / "商品期货-主力合约"


def resolve_data_path(value: str | Path | None, default: Path) -> Path:
    if value is None or str(value).strip() == "":
        return default
    return Path(value)
