"""utils/ta.py —— 技术指标工具函数（Boll / Boll3）。"""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

import pandas as pd

from utils.logger import get_logger

log = get_logger("Ta")


def round_down(value: float, tick_size: float) -> float:
    """向下取整到 tick_size 的整数倍（用于买入/多头开仓价位对齐：价格更低）。"""
    v = Decimal(str(value))
    t = Decimal(str(tick_size))
    n = (v / t).to_integral_value(rounding=ROUND_FLOOR)
    return float(n * t)


def round_up(value: float, tick_size: float) -> float:
    """向上取整到 tick_size 的整数倍（用于卖出/空头开仓价位对齐：价格更高）。"""
    v = Decimal(str(value))
    t = Decimal(str(tick_size))
    n = (v / t).to_integral_value(rounding=ROUND_CEILING)
    return float(n * t)


def Boll(
    closes: list[float],
    length: int,
    layer: int,
    tick_size: float,
    nb: float = 1.0,
) -> dict[str, list[list[float]]]:
    """多层布林带（SMA 均线，N 层对称轨道）。

    Returns:
        {
            'long':  [[第i层开仓下轨, 第i层平仓上轨], ...],
            'short': [[第i层开仓上轨, 第i层平仓下轨], ...],
            'ma': float,
            'std': float,
        }
    """
    if not closes or len(closes) < length:
        log.warning("Boll: closes 序列不足 length=%d", length)
        return {"long": [], "short": []}
    if layer <= 0:
        log.warning("Boll: layer 必须 > 0")
        return {"long": [], "short": []}
    if tick_size <= 0:
        log.warning("Boll: tick_size 必须 > 0")
        return {"long": [], "short": []}

    df = pd.DataFrame(closes, columns=["close"])
    df["ma"] = df["close"].rolling(window=length).mean()
    df["std"] = df["close"].rolling(window=length).std()

    df_last = df.tail(1)
    ma = float(df_last["ma"].values[0])
    std = float(df_last["std"].values[0])

    longs = [
        [round_down(ma - std * nb * (i + 1), tick_size), round_up(ma - std * nb * i, tick_size)]
        for i in range(layer)
    ]
    shorts = [
        [round_up(ma + std * nb * (i + 1), tick_size), round_down(ma + std * nb * i, tick_size)]
        for i in range(layer)
    ]
    return {"long": longs, "short": shorts, "ma": ma, "std": std}


def Boll3(
    closes: list[float],
    length: int,
    tick_size: float,
    nb: float = 1.0,
    nb_offset: float = 0.0,
) -> dict[str, list[list[float]]]:
    """二层布林带（EMA 均线，动态偏移外层轨道）。

    算法：
        内层宽度 = nb * std
        外层宽度 = nb * std * 2 * (1 + nb_offset)

    Returns:
        {
            'long':  [[内层下轨, 内层上轨], [外层下轨, 外层上轨]],
            'short': [[内层上轨, 内层下轨], [外层上轨, 外层下轨]],
            'ema': float,
            'std': float,
            'params': {'length': int, 'nb': float, 'nb_offset': float},
        }
    """
    if not closes or len(closes) < length:
        log.warning("Boll3: closes 序列不足 length=%d", length)
        return {"long": [], "short": []}
    if tick_size <= 0:
        log.warning("Boll3: tick_size 必须 > 0")
        return {"long": [], "short": []}

    df = pd.DataFrame(closes, columns=["close"])
    df["ema"] = df["close"].ewm(com=length, ignore_na=True).mean()
    df["std"] = df["close"].rolling(window=length).std()

    df_last = df.tail(1)
    ema = float(df_last["ema"].values[0])
    std = float(df_last["std"].values[0])

    base_level = nb * std
    outer_level = base_level * 2.0 * (1.0 + nb_offset)

    longs = [
        [round_down(ema - base_level, tick_size), round_up(ema, tick_size)],
        [round_down(ema - outer_level, tick_size), round_up(ema - base_level, tick_size)],
    ]
    shorts = [
        [round_up(ema + base_level, tick_size), round_down(ema, tick_size)],
        [round_up(ema + outer_level, tick_size), round_down(ema + base_level, tick_size)],
    ]
    return {
        "long": longs,
        "short": shorts,
        "ema": ema,
        "std": std,
        "params": {"length": length, "nb": nb, "nb_offset": nb_offset},
    }
