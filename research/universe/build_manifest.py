"""扫描 D:\\data 构建 universe manifest。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from research.common.config import MiningConfig
from research.common.contracts import parse_spread_id, product_of


@dataclass
class SpreadEntry:
    spread_id: str
    product: str
    pair_type: str
    pair_key: str
    year: str
    path: str
    sector: str = ""


def _list_calendar_entries(cfg: MiningConfig) -> list[SpreadEntry]:
    entries: list[SpreadEntry] = []
    products_filter = set(cfg.calendar_products) if cfg.calendar_products else None

    for year in cfg.years:
        year_dir = cfg.data_dir / year
        if not year_dir.is_dir():
            continue
        for prod_dir in sorted(year_dir.iterdir()):
            if not prod_dir.is_dir():
                continue
            product = prod_dir.name.upper()
            if products_filter and product not in products_filter:
                continue
            csvs = sorted(prod_dir.glob("*.csv"))
            if cfg.max_spreads_per_product > 0:
                csvs = csvs[: cfg.max_spreads_per_product]
            for fp in csvs:
                spread_id = fp.stem
                _, _, ptype = parse_spread_id(spread_id)
                entries.append(SpreadEntry(
                    spread_id=spread_id,
                    product=product,
                    pair_type=ptype,
                    pair_key=product,
                    year=year,
                    path=str(fp),
                ))
    return entries


def _list_cross_entries(cfg: MiningConfig) -> list[SpreadEntry]:
    entries: list[SpreadEntry] = []
    pairs_filter = set(cfg.cross_pairs) if cfg.cross_pairs else None

    for year in cfg.years:
        year_dir = cfg.cross_dir / year
        if not year_dir.is_dir():
            continue
        for pair_dir in sorted(year_dir.iterdir()):
            if not pair_dir.is_dir():
                continue
            pair_key = pair_dir.name
            if pairs_filter and pair_key not in pairs_filter:
                continue
            product = product_of(pair_key.split("&")[0])
            csvs = sorted(pair_dir.glob("*.csv"))
            if cfg.max_spreads_per_product > 0:
                csvs = csvs[: cfg.max_spreads_per_product]
            for fp in csvs:
                spread_id = fp.stem
                entries.append(SpreadEntry(
                    spread_id=spread_id,
                    product=product,
                    pair_type="cross",
                    pair_key=pair_key,
                    year=year,
                    path=str(fp),
                ))
    return entries


def load_industry_map(future_list_path: Path) -> dict[str, str]:
    if not future_list_path.is_file():
        return {}
    try:
        df = pd.read_csv(future_list_path, index_col=0, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(future_list_path, index_col=0, encoding="GBK")
    col = "industrySub" if "industrySub" in df.columns else "bk"
    if col not in df.columns:
        return {}
    return {str(k).upper(): str(v) for k, v in df[col].items() if pd.notna(v)}


def build_manifest(cfg: MiningConfig, future_list_path: Path | None = None) -> pd.DataFrame:
    fl_path = future_list_path or (Path(__file__).resolve().parents[2] / "config" / "future_list.csv")
    industry = load_industry_map(fl_path)

    entries = _list_calendar_entries(cfg) + _list_cross_entries(cfg)
    rows = []
    for e in entries:
        rows.append({
            "spread_id": e.spread_id,
            "product": e.product,
            "pair_type": e.pair_type,
            "pair_key": e.pair_key,
            "year": e.year,
            "path": e.path,
            "sector": industry.get(e.product.upper(), "Other"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates(subset=["spread_id", "year", "path"]).reset_index(drop=True)


def save_manifest(df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
