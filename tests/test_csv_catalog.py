"""tests/test_csv_catalog.py —— CsvCatalogSource 与全品种实例展开测试（mock 目录）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.strategy_bootstrap import bootstrap_strategy_path

bootstrap_strategy_path()

from data_sources.csv_catalog import CsvCatalogSource
from utils.contract_util import catalog_key_of


def ok(cond, msg):
    assert cond, msg


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_AB_LIST = (
    "symbol,symbol_0,symbol_1,year,source_year\n"
    "A2103&A2103,A2103,B2103,202103,2021\n"   # 跨品种 symbol 列故意写错，应被重建
    "A2105&A2105,A2105,B2105,202105,2021\n"
    "A2109&A2109,A2109,B2109,202109,2021\n"
    "A2201&A2201,A2201,B2201,202201,2021\n"
    "A2105&A2105,A2105,B2105,202105,2020\n"   # 跨 source_year 重复，应去重
)

_A_LIST = (
    "symbol,symbol_0,symbol_1,year,source_year\n"
    "A2105&A2109,A2105,A2109,202105,2021\n"
    "A2109&A2201,A2109,A2201,202109,2021\n"
    "A2201&A2205,A2201,A2205,202201,2021\n"
    "A2205&A2209,A2205,A2209,202205,2021\n"
)

_BARS = (
    "datetime,close,close_x,close_y,tradingday,trade\n"
    "2021-03-01 09:01:00,30,5800,5770,2021-03-01,1\n"
    "2021-03-01 09:02:00,32,5802,5770,2021-03-01,1\n"
)


def _make_dataset(root: Path) -> CsvCatalogSource:
    _write(root / "ContractCatalog" / "A&B_symbol_list.csv", _AB_LIST)
    _write(root / "ContractCatalog" / "A_symbol_list.csv", _A_LIST)
    _write(root / "MarketData" / "2021" / "A&B" / "A2103&B2103.csv", _BARS)
    _write(root / "MarketData" / "2021" / "A" / "A2105&A2109.csv", _BARS)
    return CsvCatalogSource(str(root))


def test_catalog_key_of():
    ok(catalog_key_of("A2103&B2103") == "A&B", "跨品种 key")
    ok(catalog_key_of("A2105&A2109") == "A", "同品种 key")


def test_list_keys():
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_dataset(Path(tmp))
        ok(src.list_catalog_keys() == ["A", "A&B"], "枚举品种键并排序")


def test_symbol_reconstruct_and_dedup():
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_dataset(Path(tmp))
        sl = src.load_symbol_list("A&B", ["2021"])
        ok("A2103&B2103" in set(sl["symbol"]), "按 symbol_0&symbol_1 重建")
        ok("A2103&A2103" not in set(sl["symbol"]), "不沿用错误 symbol 列")
        ok(sl["symbol"].is_unique, "去重后 symbol 唯一")


def test_load_spread_concat():
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_dataset(Path(tmp))
        df = src.load_spread("A&B", "A2103&B2103", ["2021"])
        ok(len(df) == 2, "读取 2 根 bar")
        ok(list(df["datetime"]) == sorted(df["datetime"]), "按 datetime 升序")
        ok(src.load_spread("A&B", "NOPE", ["2021"]).empty, "缺文件返回空")


def test_symbol_list_cached():
    """同 key 的 symbol_list 应命中进程内缓存，避免重复读盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_dataset(Path(tmp))
        first = src.load_symbol_list("A&B", ["2021"])
        second = src.load_symbol_list("A&B", ["2021"])
        ok(first is second, "二次读取命中缓存（同一对象）")
        # 删除源文件后仍能取到缓存内容，证明未再读盘
        (Path(tmp) / "ContractCatalog" / "A&B_symbol_list.csv").unlink()
        third = src.load_symbol_list("A&B", ["2021"])
        ok(third is first and not third.empty, "文件删除后仍返回缓存")


if __name__ == "__main__":
    test_catalog_key_of()
    test_list_keys()
    test_symbol_reconstruct_and_dedup()
    test_load_spread_concat()
    test_symbol_list_cached()
    print("ALL CSV CATALOG TESTS PASSED")
