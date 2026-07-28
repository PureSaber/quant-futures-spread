"""backtest/data/roll.py — 合约滚动窗口。"""
from __future__ import annotations

import datetime
from typing import List, Tuple

import pandas as pd


def gen_trade_symbol_list(df_symbol_list: pd.DataFrame,
                          params_portforlio: List[int]) -> pd.DataFrame:
    df = df_symbol_list.copy()
    df["year"] = df["year"].astype("str")
    df["datetime"] = pd.to_datetime(df["year"], format="%Y%m")
    df["end_date"] = df["datetime"] + pd.DateOffset(months=-params_portforlio[1])
    df["start_date"] = df["end_date"].shift(params_portforlio[0])
    df["start_date"] = df["start_date"].fillna(datetime.datetime(2000, 1, 1))
    df = df[["symbol", "year", "symbol_0", "symbol_1", "start_date", "end_date"]]
    return df.reset_index(drop=True)


def iter_rolls(df_trade_list: pd.DataFrame) -> List[Tuple[str, pd.Timestamp, pd.Timestamp]]:
    return [
        (row.symbol, pd.Timestamp(row.start_date), pd.Timestamp(row.end_date))
        for row in df_trade_list.itertuples()
    ]
