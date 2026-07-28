"""单因子在线计算（策略层，与 research/factors/compute.py 对齐）。"""
from __future__ import annotations

import re
from datetime import date, datetime

_CONTRACT_RE = re.compile(r"^([A-Z]+)(\d+)$")

# 原始序列 + 分位触发
QUANTILE_ENTRY_FACTORS = frozenset({
    "mom_5", "mid_dev", "depth_imb", "vol_ratio", "range_pct",
    "mom_15", "mom_60", "mom_240", "leg_mom_diff_15",
})
# 0~1 区间直接阈值
PCT_BAND_FACTORS = frozenset({"pct_rank", "boll_pct_b", "z_bid", "z_ask"})
# z-score 阈值触发
Z_ENTRY_FACTORS = frozenset({
    "z_close", "carry_ann", "seasonal_dev", "quote_width",
    "realized_vol_20", "realized_vol_120", "eff_spread",
})
# 正向 IC（高值做多）
POSITIVE_IC_FACTORS = frozenset({"depth_imb", "range_pct", "vol_ratio", "breakout_down_60"})


def _bar_field(bar, key: str, default: float = 0.0) -> float:
    if bar is None:
        return default
    if isinstance(bar, dict):
        v = bar.get(key)
    else:
        v = getattr(bar, key, None)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _contract_yyyymm(contract: str, ref_year: int) -> int | None:
    m = _CONTRACT_RE.match(str(contract).strip().upper())
    if not m:
        return None
    digits = m.group(2)
    if len(digits) == 4:
        yy, mm = int(digits[:2]), int(digits[2:])
        yyyy = 2000 + yy if yy < 80 else 1900 + yy
    elif len(digits) == 3:
        decade = (ref_year // 10) * 10
        yyyy = decade + int(digits[0])
        mm = int(digits[1:])
    else:
        return None
    if mm < 1 or mm > 12:
        return None
    return yyyy * 100 + mm


def _days_between(near_yyyymm: int, far_yyyymm: int) -> int | None:
    if far_yyyymm <= near_yyyymm:
        return None
    ny, nm = divmod(near_yyyymm, 100)
    fy, fm = divmod(far_yyyymm, 100)
    try:
        return max((date(fy, fm, 15) - date(ny, nm, 15)).days, 1)
    except ValueError:
        return None


def _zscore(series: list[float], lookback: int) -> float | None:
    if len(series) < lookback:
        return None
    w = series[-lookback:]
    mu = sum(w) / len(w)
    var = sum((x - mu) ** 2 for x in w) / len(w)
    std = var ** 0.5
    if std < 1e-12:
        return 0.0
    return (series[-1] - mu) / std


def _quantile_sorted(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    n = len(sorted_vals)
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _percentile_rank(series: list[float], lookback: int) -> float | None:
    if len(series) < lookback:
        return None
    w = series[-lookback:]
    cur = w[-1]
    less = sum(1 for x in w if x < cur)
    equal = sum(1 for x in w if x == cur)
    return (less + 0.5 * equal) / len(w)


def _band_position(val: float, lo: float, hi: float) -> float | None:
    denom = hi - lo
    if abs(denom) < 1e-12:
        return 0.5
    return (val - lo) / denom


def _mid_dev_series(bars: list[dict]) -> list[float]:
    out: list[float] = []
    for b in bars:
        c = float(b.get("close_price") or 0)
        bid = float(b.get("bid_price") or b.get("bid_low") or c)
        ask = float(b.get("ask_price") or b.get("ask_high") or c)
        out.append(c - (bid + ask) / 2.0)
    return out


def _carry_ann(close: float, symbol: str) -> float | None:
    parts = str(symbol).split("&")
    if len(parts) != 2:
        return None
    ref_year = 2024
    for p in parts:
        m = _CONTRACT_RE.match(p.strip().upper())
        if m and len(m.group(2)) >= 2:
            yy = int(m.group(2)[:2])
            ref_year = 2000 + yy if yy < 80 else 1900 + yy
            break
    near = _contract_yyyymm(parts[0], ref_year)
    far = _contract_yyyymm(parts[1], ref_year)
    if near is None or far is None:
        return None
    days = _days_between(near, far)
    if not days:
        return None
    return close / days * 365.0


def _pct_rank(closes: list[float], bar, lookback: int) -> float | None:
    c20 = _bar_field(bar, "close_20", 0.0)
    c80 = _bar_field(bar, "close_80", 0.0)
    if c80 - c20 > 1e-12:
        return (closes[-1] - c20) / (c80 - c20)
    if len(closes) < lookback:
        return None
    w = sorted(closes[-lookback:])
    q20 = _quantile_sorted(w, 0.2)
    q80 = _quantile_sorted(w, 0.8)
    denom = q80 - q20
    if abs(denom) < 1e-12:
        return 0.5
    return (closes[-1] - q20) / denom


def _mom_series(closes: list[float], h: int) -> list[float]:
    if len(closes) <= h:
        return []
    return [closes[i] - closes[i - h] for i in range(h, len(closes))]


def _leg_mom_diff_series(bars: list[dict], h: int = 15) -> list[float]:
    xs = [float(b.get("leg_close_x") or 0) for b in bars]
    ys = [float(b.get("leg_close_y") or 0) for b in bars]
    if not any(xs) or not any(ys) or len(xs) <= h:
        return []
    return [xs[i] - xs[i - h] - (ys[i] - ys[i - h]) for i in range(h, len(xs))]


def _range_pct_series(bars: list[dict]) -> list[float]:
    out: list[float] = []
    for b in bars:
        c = abs(float(b.get("close_price") or 0))
        hi = float(b.get("high_price") or c)
        lo = float(b.get("low_price") or c)
        out.append((hi - lo) / c if c > 1e-12 else 0.0)
    return out


def _depth_imb(bar) -> float | None:
    bv = _bar_field(bar, "bid_volume", 0.0)
    av = _bar_field(bar, "ask_volume", 0.0)
    tot = bv + av
    if tot < 1e-12:
        return None
    return bv / tot - 0.5


def _depth_imb_series(bars: list[dict]) -> list[float]:
    out: list[float] = []
    for b in bars:
        bv = float(b.get("bid_volume") or 0)
        av = float(b.get("ask_volume") or 0)
        tot = bv + av
        out.append(bv / tot - 0.5 if tot > 1e-12 else 0.0)
    return out


def _realized_vol_series(closes: list[float], win: int) -> list[float]:
    if len(closes) < win + 1:
        return []
    rets = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    out: list[float] = []
    for i in range(win - 1, len(rets)):
        w = rets[i - win + 1: i + 1]
        mu = sum(w) / len(w)
        var = sum((x - mu) ** 2 for x in w) / len(w)
        out.append(var ** 0.5)
    return out


def _seasonal_dev(closes: list[float], bars: list[dict], lookback: int) -> float | None:
    if len(closes) < lookback or len(bars) < lookback:
        return None
    buckets: dict[int, list[float]] = {}
    for b, c in zip(bars[-lookback:], closes[-lookback:]):
        dt_raw = b.get("datetime")
        if not dt_raw:
            continue
        if isinstance(dt_raw, str):
            dt = datetime.fromisoformat(dt_raw.replace(" ", "T", 1) if "T" not in dt_raw else dt_raw)
        else:
            dt = dt_raw
        mod = dt.hour * 60 + dt.minute
        buckets.setdefault(mod, []).append(c)
    last = bars[-1].get("datetime")
    if not last:
        return None
    if isinstance(last, str):
        dt = datetime.fromisoformat(last.replace(" ", "T", 1) if "T" not in last else last)
    else:
        dt = last
    mod = dt.hour * 60 + dt.minute
    hist = buckets.get(mod, [])
    if len(hist) < 3:
        return None
    seasonal = sum(hist) / len(hist)
    return closes[-1] - seasonal


def compute_factor_value(
    factor: str,
    bars: list[dict],
    bar,
    symbol: str,
    lookback: int,
    vol_short: int = 20,
    vol_long: int = 120,
) -> float | None:
    if not bars:
        return None
    closes = [float(b["close_price"]) for b in bars if b.get("close_price") is not None]
    lows = [float(b.get("low_price") or b["close_price"]) for b in bars]
    highs = [float(b.get("high_price") or b["close_price"]) for b in bars]
    if len(closes) < lookback // 2:
        return None

    name = str(factor).strip().lower()

    if name == "z_close":
        return _zscore(closes, lookback)

    if name == "mid_dev":
        series = _mid_dev_series(bars)
        return series[-1] if series else None

    if name == "pct_rank":
        return _pct_rank(closes, bar, lookback)

    if name == "mom_5":
        return closes[-1] - closes[-6] if len(closes) >= 6 else None

    if name == "mom_15":
        return closes[-1] - closes[-16] if len(closes) >= 16 else None

    if name == "mom_60":
        return closes[-1] - closes[-61] if len(closes) >= 61 else None

    if name == "mom_240":
        return closes[-1] - closes[-241] if len(closes) >= 241 else None

    if name == "leg_mom_diff_15":
        series = _leg_mom_diff_series(bars, 15)
        return series[-1] if series else None

    if name == "carry_ann":
        series = []
        for b in bars:
            c = float(b["close_price"])
            v = _carry_ann(c, symbol)
            if v is not None:
                series.append(v)
        return _zscore(series, lookback) if len(series) >= lookback else None

    if name == "breakout_down_60":
        win = min(60, len(lows))
        return 1.0 if closes[-1] <= min(lows[-win:]) + 1e-9 else 0.0

    if name == "breakout_up_60":
        win = min(60, len(highs))
        return 1.0 if closes[-1] >= max(highs[-win:]) - 1e-9 else 0.0

    if name == "boll_pct_b":
        if len(closes) < lookback:
            return None
        w = closes[-lookback:]
        mu = sum(w) / len(w)
        var = sum((x - mu) ** 2 for x in w) / len(w)
        sd = var ** 0.5
        denom = 4 * sd
        if abs(denom) < 1e-12:
            return 0.5
        return (closes[-1] - (mu - 2 * sd)) / denom

    if name == "z_bid":
        bid = _bar_field(bar, "bid_price", 0.0)
        b20 = _bar_field(bar, "bid_price_20", 0.0)
        b80 = _bar_field(bar, "bid_price_80", 0.0)
        pos = _band_position(bid, b20, b80)
        return pos

    if name == "z_ask":
        ask = _bar_field(bar, "ask_price", 0.0)
        a20 = _bar_field(bar, "ask_price_20", 0.0)
        a80 = _bar_field(bar, "ask_price_80", 0.0)
        return _band_position(ask, a20, a80)

    if name == "depth_imb":
        return _depth_imb(bar)

    if name == "range_pct":
        c = abs(closes[-1])
        if c < 1e-12:
            return None
        return (highs[-1] - lows[-1]) / c

    if name == "vol_ratio":
        rv20 = _realized_vol_series(closes, vol_short)
        rv120 = _realized_vol_series(closes, vol_long)
        if not rv20 or not rv120:
            return None
        denom = rv120[-1]
        return rv20[-1] / denom if abs(denom) > 1e-12 else None

    if name == "realized_vol_20":
        series = _realized_vol_series(closes, vol_short)
        return _zscore(series, lookback) if len(series) >= lookback else None

    if name == "realized_vol_120":
        series = _realized_vol_series(closes, vol_long)
        return _zscore(series, lookback) if len(series) >= lookback else None

    if name == "eff_spread":
        ask = _bar_field(bar, "ask_price", 0.0) or _bar_field(bar, "ask_high", 0.0)
        bid = _bar_field(bar, "bid_price", 0.0) or _bar_field(bar, "bid_low", 0.0)
        return ask - bid

    if name == "quote_width":
        a80 = _bar_field(bar, "ask_price_80", 0.0) or _bar_field(bar, "ask_high", 0.0)
        b20 = _bar_field(bar, "bid_price_20", 0.0) or _bar_field(bar, "bid_low", 0.0)
        return a80 - b20

    if name == "seasonal_dev":
        return _seasonal_dev(closes, bars, lookback)

    return None


def compute_entry_rank(
    factor: str,
    bars: list[dict],
    bar,
    symbol: str,
    lookback: int,
) -> float | None:
    name = str(factor).strip().lower()
    if name in QUANTILE_ENTRY_FACTORS:
        if name == "mid_dev":
            series = _mid_dev_series(bars)
        elif name == "mom_5":
            closes = [float(b["close_price"]) for b in bars if b.get("close_price") is not None]
            series = _mom_series(closes, 5)
        elif name == "mom_15":
            closes = [float(b["close_price"]) for b in bars if b.get("close_price") is not None]
            series = _mom_series(closes, 15)
        elif name == "mom_60":
            closes = [float(b["close_price"]) for b in bars if b.get("close_price") is not None]
            series = _mom_series(closes, 60)
        elif name == "mom_240":
            closes = [float(b["close_price"]) for b in bars if b.get("close_price") is not None]
            series = _mom_series(closes, 240)
        elif name == "depth_imb":
            series = _depth_imb_series(bars)
        elif name == "range_pct":
            series = _range_pct_series(bars)
        elif name == "vol_ratio":
            closes = [float(b["close_price"]) for b in bars if b.get("close_price") is not None]
            rv20 = _realized_vol_series(closes, 20)
            rv120 = _realized_vol_series(closes, 120)
            n = min(len(rv20), len(rv120))
            if n < lookback // 2:
                return None
            series = [rv20[-n + i] / rv120[-n + i] if abs(rv120[-n + i]) > 1e-12 else 0.0 for i in range(n)]
        elif name == "leg_mom_diff_15":
            series = _leg_mom_diff_series(bars, 15)
        else:
            return None
        if not series:
            return None
        return _percentile_rank(series, min(lookback, len(series)))
    return compute_factor_value(factor, bars, bar, symbol, lookback)


def factor_ic_sign(factor: str) -> int:
    if str(factor).strip().lower() in POSITIVE_IC_FACTORS:
        return 1
    return -1


def factor_uses_quantile_entry(factor: str) -> bool:
    return str(factor).strip().lower() in QUANTILE_ENTRY_FACTORS


def factor_uses_pct_band(factor: str) -> bool:
    return str(factor).strip().lower() in PCT_BAND_FACTORS


__all__ = [
    "QUANTILE_ENTRY_FACTORS",
    "PCT_BAND_FACTORS",
    "Z_ENTRY_FACTORS",
    "POSITIVE_IC_FACTORS",
    "compute_entry_rank",
    "compute_factor_value",
    "factor_ic_sign",
    "factor_uses_quantile_entry",
    "factor_uses_pct_band",
]
