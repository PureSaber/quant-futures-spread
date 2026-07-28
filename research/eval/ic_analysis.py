"""IC / RankIC / 分层检验。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.factors.registry import factor_names


def _rank_corr(x: pd.Series, y: pd.Series) -> float:
    if len(x) < 10:
        return np.nan
    if x.nunique() < 2 or y.nunique() < 2:
        return np.nan
    rx = x.rank(method="average")
    ry = y.rank(method="average")
    ic = rx.corr(ry)
    return float(ic) if np.isfinite(ic) else np.nan


def _daily_rank_ic(day_df: pd.DataFrame, factor: str, label: str) -> float:
    sub = day_df[[factor, label]].dropna()
    return _rank_corr(sub[factor], sub[label])


def _label_col(horizon: int, label_template: str) -> str:
    return label_template.format(h=horizon)


def compute_ic_panel(
    factor_df: pd.DataFrame,
    horizons: list[int],
    factor_cols: list[str] | None = None,
    label_template: str = "fwd_ret_{h}",
    label_kind: str = "mid",
) -> pd.DataFrame:
    """每个 spread 计算全样本 Rank IC，并附日截面 IC（当同日多 spread 时）。

    label_template: 如 ``fwd_ret_{h}`` 或 ``fwd_realized_long_{h}``
    label_kind: 写入结果列 ``label_kind``，便于合并对比（mid / realized_long）
    """
    factors = factor_cols or factor_names()
    rows: list[dict] = []

    for spread_id, g in factor_df.groupby("spread_id"):
        sector = g["sector"].iloc[0] if "sector" in g.columns else "Other"
        product = g["product"].iloc[0] if "product" in g.columns else ""
        for h in horizons:
            label = _label_col(h, label_template)
            if label not in g.columns:
                continue
            for fac in factors:
                if fac not in g.columns:
                    continue
                sub = g[[fac, label]].dropna()
                ic = _rank_corr(sub[fac], sub[label])
                if np.isfinite(ic):
                    rows.append({
                        "spread_id": spread_id,
                        "product": product,
                        "sector": sector,
                        "factor": fac,
                        "horizon": h,
                        "rank_ic": ic,
                        "n": len(sub),
                        "ic_type": "timeseries",
                        "label_kind": label_kind,
                    })

    # 日截面：每个交易日各 spread 取当日最后一个有效 bar
    if "tradingday" in factor_df.columns and factor_df["spread_id"].nunique() >= 2:
        eod = (
            factor_df.sort_values("datetime")
            .groupby(["tradingday", "spread_id"], as_index=False)
            .last()
        )
        for h in horizons:
            label = _label_col(h, label_template)
            if label not in eod.columns:
                continue
            for fac in factors:
                if fac not in eod.columns:
                    continue
                for td, day_g in eod.groupby("tradingday"):
                    if day_g["spread_id"].nunique() < 2:
                        continue
                    ic = _daily_rank_ic(day_g, fac, label)
                    if np.isfinite(ic):
                        rows.append({
                            "spread_id": "cross_section",
                            "product": "",
                            "sector": day_g["sector"].iloc[0] if "sector" in day_g.columns else "",
                            "factor": fac,
                            "horizon": h,
                            "rank_ic": ic,
                            "n": len(day_g),
                            "ic_type": "cross_section",
                            "label_kind": label_kind,
                            "tradingday": td,
                        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def summarize_ic(ic_df: pd.DataFrame) -> pd.DataFrame:
    if ic_df.empty:
        return pd.DataFrame()
    ts = ic_df[ic_df["ic_type"] == "timeseries"] if "ic_type" in ic_df.columns else ic_df
    if ts.empty:
        ts = ic_df
    group_cols = ["factor", "horizon"]
    if "label_kind" in ts.columns:
        group_cols.append("label_kind")
    g = ts.groupby(group_cols)["rank_ic"]
    out = g.agg(ic_mean="mean", ic_std="std", ic_count="count").reset_index()
    out["icir"] = out["ic_mean"] / out["ic_std"].replace(0, np.nan)
    out["abs_ic_mean"] = out["ic_mean"].abs()
    sort_cols = ["horizon", "abs_ic_mean"]
    if "label_kind" in out.columns:
        sort_cols = ["label_kind"] + sort_cols
    return out.sort_values(sort_cols, ascending=[True, True, False])


def compare_ic_summaries(mid_ic: pd.DataFrame, realized_ic: pd.DataFrame) -> pd.DataFrame:
    """并排对比中间价 fwd_ret 与可实现 fwd_realized_long IC。"""
    if mid_ic.empty or realized_ic.empty:
        return pd.DataFrame()
    left = mid_ic.copy()
    right = realized_ic.copy()
    if "label_kind" in left.columns:
        left = left.drop(columns=["label_kind"])
    if "label_kind" in right.columns:
        right = right.drop(columns=["label_kind"])
    left = left.rename(columns={
        c: f"mid_{c}" for c in left.columns if c not in ("factor", "horizon")
    })
    right = right.rename(columns={
        c: f"realized_{c}" for c in right.columns if c not in ("factor", "horizon")
    })
    merged = pd.merge(left, right, on=["factor", "horizon"], how="outer")
    if "mid_ic_mean" in merged.columns and "realized_ic_mean" in merged.columns:
        merged["ic_decay"] = merged["realized_ic_mean"] - merged["mid_ic_mean"]
        merged["sign_flip"] = (
            merged["mid_ic_mean"].fillna(0) * merged["realized_ic_mean"].fillna(0) < 0
        )
    return merged.sort_values(["horizon", "mid_abs_ic_mean"], ascending=[True, False], na_position="last")


def quantile_spread_test(
    factor_df: pd.DataFrame,
    factor: str,
    label: str,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    sub = factor_df[[factor, label, "spread_id"]].dropna()
    if len(sub) < n_quantiles * 10:
        return pd.DataFrame()
    try:
        sub = sub.copy()
        sub["q"] = pd.qcut(sub[factor], n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    return sub.groupby("q")[label].agg(["mean", "count"]).reset_index()


def filter_redundant_factors(
    factor_df: pd.DataFrame,
    candidate_factors: list[str],
    anchor: str = "z_close",
    max_corr: float = 0.95,
    sample_rows: int = 50_000,
) -> list[str]:
    """剔除与 anchor 因子高度相关的冗余因子。"""
    cols = [c for c in candidate_factors if c in factor_df.columns]
    if anchor not in factor_df.columns or not cols:
        return cols
    sub = factor_df[[anchor] + cols].dropna()
    if len(sub) > sample_rows:
        sub = sub.sample(sample_rows, random_state=42)
    if len(sub) < 100:
        return cols
    kept = []
    anchor_s = sub[anchor]
    if isinstance(anchor_s, pd.DataFrame):
        anchor_s = anchor_s.iloc[:, 0]
    for fac in cols:
        if fac == anchor:
            if fac not in kept:
                kept.append(fac)
            continue
        if fac not in sub.columns:
            continue
        fac_s = sub[fac]
        if isinstance(fac_s, pd.DataFrame):
            fac_s = fac_s.iloc[:, 0]
        c = abs(anchor_s.corr(fac_s))
        if not np.isfinite(c) or c <= max_corr:
            kept.append(fac)
    return kept


def ic_by_sector(ic_df: pd.DataFrame) -> pd.DataFrame:
    if ic_df.empty or "sector" not in ic_df.columns:
        return pd.DataFrame()
    ts = ic_df[ic_df.get("ic_type", "timeseries") == "timeseries"]
    if ts.empty:
        return pd.DataFrame()
    return ts.groupby(["sector", "factor", "horizon"])["rank_ic"].mean().reset_index()
