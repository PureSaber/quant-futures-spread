"""backtest/panel/sector_panel.py —— SpreadSectorPanel：日收盘截面 + sector extreme cache。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from utils.contract_util import catalog_key_of, product_of, split_spread
from utils.spread_sector import build_board_peers
from utils.logger import get_logger

logger = get_logger("SpreadSectorPanel")

_EXCLUDED_SECTOR = "中金所"


def _sector_of_product(product: str, sector_map: Dict[str, str]) -> str:
    return sector_map.get(str(product).upper(), "其他")


def _normalize_minute_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "datetime" in out.columns:
        out["_dt"] = pd.to_datetime(out["datetime"])
        out = out.set_index("_dt")
    elif not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    if "tradingday" not in out.columns:
        raise ValueError("缺少 tradingday 列")
    out["tradingday"] = out["tradingday"].astype(str)
    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        elif col == "volume":
            out[col] = 0.0
    return out.sort_index()


def _daily_last_close(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="float64")
    out = df.copy()
    if "tradingday" not in out.columns:
        raise ValueError("缺少 tradingday 列")
    close_col = "close" if "close" in out.columns else None
    if close_col is None:
        raise ValueError("缺少 close 列")
    out["_td"] = pd.to_datetime(out["tradingday"])
    out["_c"] = pd.to_numeric(out[close_col], errors="coerce")
    g = out.sort_values("_td").groupby("_td")["_c"].last()
    g.index = g.index.normalize()
    return g


def build_sector_extreme_cache(
    daily_close: pd.DataFrame,
    sector_map: Dict[str, str],
) -> Dict[str, Dict[int, int]]:
    """日频 close 面板 → 各板块 prior-day extreme 映射 {sector: {day_i64: ext}}。"""
    if daily_close is None or daily_close.empty:
        return {}
    px = daily_close.sort_index()
    rets = px.pct_change(fill_method=None)
    col_to_sec: Dict[str, str] = {}
    for c in px.columns:
        col = str(c)
        if "&" in col:
            leg0, _ = split_spread(col)
            col_to_sec[col] = _sector_of_product(product_of(leg0), sector_map)
        else:
            col_to_sec[col] = _sector_of_product(col, sector_map)
    sec_groups: Dict[str, list] = {}
    for c, sec in col_to_sec.items():
        if sec == _EXCLUDED_SECTOR:
            continue
        sec_groups.setdefault(sec, []).append(c)
    if not sec_groups:
        return {}
    sec_ret = pd.DataFrame(
        {sec: rets[cols].mean(axis=1) for sec, cols in sec_groups.items() if cols}
    )
    if sec_ret.empty:
        return {}
    shifted = sec_ret.shift(1)
    q90 = shifted.quantile(0.9, axis=1)
    q10 = shifted.quantile(0.1, axis=1)
    out: Dict[str, Dict[int, int]] = {}
    day_i64 = px.index.astype("datetime64[ns]").astype("int64").to_numpy()
    for sec in shifted.columns:
        s = shifted[sec]
        arr = np.zeros(len(s), dtype=np.int8)
        arr[(s > q90).fillna(False)] = 1
        arr[(s < q10).fillna(False)] = -1
        out[str(sec)] = {int(day_i64[i]): int(arr[i]) for i in range(len(arr))}
    return out


@dataclass
class SpreadSectorPanel:
    """截面 panel：sector extreme（zhouhaotian）+ board/minute（xuhe 等）。"""

    sector_ext_by_sector: Dict[str, Dict[int, int]] = field(default_factory=dict)
    spread_to_sector: Dict[str, str] = field(default_factory=dict)
    sector_map: Dict[str, str] = field(default_factory=dict)
    minute_by_spread: Dict[str, pd.DataFrame] = field(default_factory=dict)
    board_peers: Dict[str, List[str]] = field(default_factory=dict)
    industry_map: Dict[str, str] = field(default_factory=dict)
    _morning_pool_cache: dict = field(default_factory=dict, repr=False)

    def sector_ext_for(self, spread_symbol: str, tradingday) -> int:
        sec = self.spread_to_sector.get(str(spread_symbol))
        if not sec:
            leg0, _ = split_spread(str(spread_symbol))
            p0 = product_of(leg0)
            sec = _sector_of_product(p0, self.sector_map)
        tday_i64 = int(pd.Timestamp(tradingday).normalize().value)
        return int(self.sector_ext_by_sector.get(sec, {}).get(tday_i64, 0))

    def sector_ext_map_for_spread(self, spread_symbol: str) -> Dict[int, int]:
        sec = self.spread_to_sector.get(str(spread_symbol))
        if not sec:
            leg0, _ = split_spread(str(spread_symbol))
            p0 = product_of(leg0)
            sec = _sector_of_product(p0, self.sector_map)
        return dict(self.sector_ext_by_sector.get(sec, {}))

    def board_peers_for(self, spread_symbol: str) -> List[str]:
        return list(self.board_peers.get(str(spread_symbol), []))

    def minute_df(self, spread_symbol: str) -> pd.DataFrame:
        return self.minute_by_spread.get(str(spread_symbol), pd.DataFrame())

    def peer_close_at(self, spread_symbol: str, ts) -> Optional[float]:
        df = self.minute_by_spread.get(str(spread_symbol))
        if df is None or df.empty:
            return None
        t = pd.Timestamp(ts)
        if t in df.index:
            v = df.at[t, "close"]
            return None if pd.isna(v) else float(v)
        return None

    def peer_first_close_on_day(
        self,
        spread_symbol: str,
        tradingday: str,
        before_ts=None,
    ) -> Optional[float]:
        df = self.minute_by_spread.get(str(spread_symbol))
        if df is None or df.empty:
            return None
        td = str(tradingday)
        sub = df[df["tradingday"] == td]
        if before_ts is not None:
            sub = sub[sub.index <= pd.Timestamp(before_ts)]
        if sub.empty:
            return None
        v = sub.iloc[0]["close"]
        return None if pd.isna(v) else float(v)

    def bars_on_day_before(
        self,
        spread_symbol: str,
        tradingday: str,
        before_ts,
    ) -> pd.DataFrame:
        df = self.minute_by_spread.get(str(spread_symbol))
        if df is None or df.empty:
            return pd.DataFrame()
        sub = df[(df["tradingday"] == str(tradingday)) & (df.index < pd.Timestamp(before_ts))]
        return sub

    def warmup_minute_df(self, spread_symbol: str) -> pd.DataFrame:
        return self.minute_df(spread_symbol).copy()

    def morning_pool_masks(self, pool_mode: str = "cv80"):
        """全 universe 09:00–09:29 CV/T 截面池 bool 矩阵（按 pool_mode 缓存）。"""
        key = str(pool_mode).lower()
        cached = self._morning_pool_cache.get(key)
        if cached is not None:
            return cached
        from strategies.wangzhihao.cross_section import build_morning_pool_masks

        symbols = list(self.minute_by_spread.keys())
        result = build_morning_pool_masks(self.minute_by_spread, symbols, pool_mode)
        self._morning_pool_cache[key] = result
        return result


def build_panel_from_schedule(
    schedule,
    products: list[str],
    years: list[str],
    cfg,
    fl,
    source,
    sector_map: Dict[str, str],
    industry_map: Optional[Dict[str, str]] = None,
) -> Optional[SpreadSectorPanel]:
    """从 SpreadSchedule 预载各 product 日 spread 分钟/日收盘。"""
    industry_map = dict(industry_map or {})
    spreads: set[str] = set()
    for prod in products:
        for sym in schedule.unique_spreads(prod, years):
            spreads.add(sym)

    if not spreads:
        return None

    daily_frames: dict[str, pd.Series] = {}
    minute_frames: dict[str, pd.DataFrame] = {}
    for spread in sorted(spreads):
        leg0, _ = split_spread(spread)
        product = product_of(leg0)
        if cfg.products and product not in cfg.products:
            continue
        if product in cfg.exclude:
            continue
        key = catalog_key_of(spread)
        try:
            df = source.load_spread(key, spread, cfg.years)
        except Exception:  # noqa: BLE001
            logger.warning(f"panel 跳过 spread={spread}（加载失败）")
            continue
        if df is None or df.empty:
            continue
        try:
            minute_frames[spread] = _normalize_minute_df(df)
            daily_frames[spread] = _daily_last_close(df)
        except Exception:  # noqa: BLE001
            logger.warning(f"panel 跳过 spread={spread}（规范化失败）")

    if not daily_frames and not minute_frames:
        logger.warning("SpreadSectorPanel：无可用数据")
        return SpreadSectorPanel(sector_map=dict(sector_map), industry_map=industry_map)

    daily_close = pd.DataFrame(daily_frames).sort_index() if daily_frames else pd.DataFrame()
    sector_ext = build_sector_extreme_cache(daily_close, sector_map) if not daily_close.empty else {}
    spread_to_sector = {}
    for spread in set(daily_frames) | set(minute_frames):
        leg0, _ = split_spread(spread)
        spread_to_sector[spread] = _sector_of_product(product_of(leg0), sector_map)

    board_peers = build_board_peers(list(minute_frames.keys()), industry_map) if industry_map else {}

    return SpreadSectorPanel(
        sector_ext_by_sector=sector_ext,
        spread_to_sector=spread_to_sector,
        sector_map=dict(sector_map),
        minute_by_spread=minute_frames,
        board_peers=board_peers,
        industry_map=industry_map,
    )


def build_panel_from_entries(
    entries: Iterable[dict],
    cfg,
    fl,
    source,
    sector_map: Dict[str, str],
    industry_map: Optional[Dict[str, str]] = None,
) -> Optional[SpreadSectorPanel]:
    """从回测实例列表预载 spread 分钟/日收盘，构建截面 panel。"""
    industry_map = dict(industry_map or {})
    spreads: dict[str, str] = {}
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        params = dict(entry.get("params") or {})
        sym = str(params.get("symbol") or "").strip()
        if sym:
            spreads[sym] = sym

    if not spreads:
        return None

    daily_frames: dict[str, pd.Series] = {}
    minute_frames: dict[str, pd.DataFrame] = {}
    for spread in spreads:
        leg0, _ = split_spread(spread)
        product = product_of(leg0)
        if cfg.products and product not in cfg.products:
            continue
        if product in cfg.exclude:
            continue
        key = catalog_key_of(spread)
        try:
            df = source.load_spread(key, spread, cfg.years)
        except Exception:  # noqa: BLE001
            logger.warning(f"panel 跳过 spread={spread}（加载失败）")
            continue
        if df is None or df.empty:
            continue
        try:
            minute_frames[spread] = _normalize_minute_df(df)
            daily_frames[spread] = _daily_last_close(df)
        except Exception:  # noqa: BLE001
            logger.warning(f"panel 跳过 spread={spread}（规范化失败）")

    if not daily_frames and not minute_frames:
        logger.warning("SpreadSectorPanel：无可用数据")
        return SpreadSectorPanel(sector_map=dict(sector_map), industry_map=industry_map)

    daily_close = pd.DataFrame(daily_frames).sort_index() if daily_frames else pd.DataFrame()
    sector_ext = build_sector_extreme_cache(daily_close, sector_map) if not daily_close.empty else {}
    spread_to_sector = {}
    for spread in set(daily_frames) | set(minute_frames):
        leg0, _ = split_spread(spread)
        spread_to_sector[spread] = _sector_of_product(product_of(leg0), sector_map)

    board_peers = build_board_peers(list(minute_frames.keys()), industry_map) if industry_map else {}

    return SpreadSectorPanel(
        sector_ext_by_sector=sector_ext,
        spread_to_sector=spread_to_sector,
        sector_map=dict(sector_map),
        minute_by_spread=minute_frames,
        board_peers=board_peers,
        industry_map=industry_map,
    )


def strategy_needs_panel(module_path: str) -> bool:
    mod = str(module_path or "")
    if "gaozong2_spread" in mod or "tangye2_spread" in mod:
        return False
    return "zhouhaotian" in mod or "xuhe" in mod or "tangye2" in mod \
        or "gaozong2" in mod or "wangzhihao" in mod


__all__ = [
    "SpreadSectorPanel",
    "build_panel_from_entries",
    "build_panel_from_schedule",
    "build_sector_extreme_cache",
    "strategy_needs_panel",
]
