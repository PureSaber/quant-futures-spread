"""单轮因子挖掘入口。"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from research.common.config import MiningConfig, load_config
from research.common.loader import add_forward_labels, apply_filters, load_spread_csv
from research.eval.ic_analysis import (
    compare_ic_summaries,
    compute_ic_panel,
    filter_redundant_factors,
    ic_by_sector,
    quantile_spread_test,
    summarize_ic,
)
from research.eval.interpretability import generate_report
from research.factors.compute import compute_factors
from research.factors.registry import factor_names
from research.universe.build_manifest import build_manifest
from research.universe.roll_dates import load_roll_dates


def _process_one_spread(
    row: pd.Series,
    cfg: MiningConfig,
) -> pd.DataFrame | None:
    path = Path(row["path"])
    if not path.is_file():
        return None
    try:
        df = load_spread_csv(path)
    except Exception:
        return None
    df = apply_filters(df, cfg.require_trade, cfg.max_eff_spread_ticks)
    if len(df) < cfg.lookback + max(cfg.horizons_min) + 10:
        return None

    file_year = int(row["year"])
    roll_dates = None
    if row["pair_type"] == "calendar":
        roll_dates = load_roll_dates(cfg.dom_table_dir, row["product"], [row["year"]])

    df = add_forward_labels(df, cfg.horizons_min)
    df = compute_factors(
        df,
        spread_id=row["spread_id"],
        file_year=file_year,
        lookback=cfg.lookback,
        vol_short=cfg.vol_short,
        vol_long=cfg.vol_long,
        roll_dates=roll_dates,
    )

    # 全量时每 spread 最多保留 5000 行以控制内存
    if cfg.mode == "full" and len(df) > 5000:
        idx = np.linspace(0, len(df) - 1, 5000, dtype=int)
        df = df.iloc[idx].reset_index(drop=True)

    fac_cols = factor_names()
    label_cols = [c for c in df.columns if c.startswith("fwd_")]
    keep = ["datetime", "tradingday"] + [c for c in fac_cols if c in df.columns] + label_cols
    df = df[keep].copy()
    df["spread_id"] = row["spread_id"]
    df["product"] = row["product"]
    df["pair_type"] = row["pair_type"]
    df["sector"] = row.get("sector", "Other")
    df["year"] = row["year"]
    return df


def _worker(row_dict: dict, cfg: MiningConfig) -> pd.DataFrame | None:
    return _process_one_spread(pd.Series(row_dict), cfg)


def _process_manifest(manifest: pd.DataFrame, cfg: MiningConfig) -> list[pd.DataFrame]:
    chunks: list[pd.DataFrame] = []
    rows = [r._asdict() if hasattr(r, "_asdict") else r.to_dict() for _, r in manifest.iterrows()]
    workers = 6 if cfg.mode == "full" else 1
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, row, cfg): i for i, row in enumerate(rows)}
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 100 == 0 or done == len(rows):
                    print(f"  完成 {done}/{len(rows)} spreads", flush=True)
                part = fut.result()
                if part is not None and not part.empty:
                    chunks.append(part)
    else:
        for i, row in enumerate(rows):
            if i == 0 or (i + 1) % 10 == 0:
                print(f"  处理 spread {i + 1}/{len(rows)}")
            part = _worker(row, cfg)
            if part is not None and not part.empty:
                chunks.append(part)
    return chunks


def run_round(config_path: str | Path) -> Path:
    t0 = time.time()
    cfg = load_config(config_path)
    out_dir = cfg.round_output
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{cfg.round_id}] 构建宇宙 manifest ...", flush=True)
    manifest = build_manifest(cfg)
    manifest.to_parquet(out_dir / "universe_manifest.parquet", index=False)
    print(f"  spreads: {len(manifest)}", flush=True)
    if manifest.empty:
        raise SystemExit("宇宙为空，请检查 data_dir / years 配置")

    chunks = _process_manifest(manifest, cfg)

    if not chunks:
        raise SystemExit("无有效因子样本")

    factor_df = pd.concat(chunks, ignore_index=True)
    factor_path = out_dir / "factor_panel.parquet"
    factor_df.to_parquet(factor_path, index=False)
    print(f"  factor_panel rows: {len(factor_df):,}")

    print(f"[{cfg.round_id}] 计算 IC ...")
    ic_raw = compute_ic_panel(
        factor_df, cfg.horizons_min,
        label_template="fwd_ret_{h}", label_kind="mid",
    )
    ic_summary = summarize_ic(ic_raw)
    ic_raw.to_parquet(out_dir / "ic_daily.parquet", index=False)
    ic_summary.to_csv(out_dir / "ic_summary.csv", index=False)

    ic_raw_realized = compute_ic_panel(
        factor_df, cfg.horizons_min,
        label_template="fwd_realized_long_{h}", label_kind="realized_long",
    )
    ic_summary_realized = summarize_ic(ic_raw_realized)
    ic_compare = pd.DataFrame()
    if not ic_raw_realized.empty:
        ic_raw_realized.to_parquet(out_dir / "ic_daily_realized.parquet", index=False)
    if not ic_summary_realized.empty:
        ic_summary_realized.to_csv(out_dir / "ic_summary_realized.csv", index=False)
    ic_compare = compare_ic_summaries(ic_summary, ic_summary_realized)
    if not ic_compare.empty:
        ic_compare.to_csv(out_dir / "ic_compare_mid_vs_realized.csv", index=False)

    sector_ic = ic_by_sector(ic_raw)
    if not sector_ic.empty:
        sector_ic.to_csv(out_dir / "ic_by_sector.csv", index=False)

    # 分层检验：取得分最高的 3 个因子（中间价 + 可实现）
    quantile_results: dict[str, pd.DataFrame] = {}
    if not ic_summary.empty:
        top_factors = ic_summary.nlargest(3, "abs_ic_mean")
        for _, r in top_factors.iterrows():
            fac, h = r["factor"], int(r["horizon"])
            for label_tpl, suffix in [("fwd_ret_{h}", "mid"), ("fwd_realized_long_{h}", "realized")]:
                label = label_tpl.format(h=h)
                if label not in factor_df.columns:
                    continue
                key = f"{fac}_h{h}_{suffix}"
                quantile_results[key] = quantile_spread_test(factor_df, fac, label)

    # 入选（中间价 IC）
    selected = pd.DataFrame()
    if not ic_summary.empty:
        selected = ic_summary[
            (ic_summary["abs_ic_mean"] >= cfg.min_abs_ic)
            & (ic_summary["icir"].abs() >= cfg.min_icir)
        ].copy()
        if "label_kind" in selected.columns:
            selected = selected[selected["label_kind"] == "mid"] if "mid" in selected["label_kind"].values else selected
        if not selected.empty and "z_close" in factor_df.columns:
            facs = selected["factor"].unique().tolist()
            kept = filter_redundant_factors(
                factor_df, facs, anchor="z_close", max_corr=cfg.max_corr_with_z_close,
            )
            selected = selected[selected["factor"].isin(kept)]
        selected.to_csv(out_dir / "selected_factors.csv", index=False)

    # 入选（可实现 IC，Round2）
    selected_realized = pd.DataFrame()
    if not ic_summary_realized.empty and cfg.min_abs_realized_ic > 0:
        selected_realized = ic_summary_realized[
            (ic_summary_realized["abs_ic_mean"] >= cfg.min_abs_realized_ic)
            & (ic_summary_realized["icir"].abs() >= cfg.min_realized_icir)
        ].copy()
        if "label_kind" in selected_realized.columns:
            selected_realized = selected_realized[
                selected_realized["label_kind"] == "realized_long"
            ] if "realized_long" in selected_realized["label_kind"].values else selected_realized
        if not selected_realized.empty and "z_close" in factor_df.columns:
            facs = selected_realized["factor"].unique().tolist()
            kept = filter_redundant_factors(
                factor_df, facs, anchor="z_close", max_corr=cfg.max_corr_with_z_close,
            )
            selected_realized = selected_realized[selected_realized["factor"].isin(kept)]
        selected_realized.to_csv(out_dir / "selected_factors_realized.csv", index=False)

    duration = time.time() - t0
    report = generate_report(
        cfg.round_id,
        cfg.mode,
        ic_summary,
        sector_ic,
        quantile_results,
        manifest,
        selected,
        out_dir,
        duration,
        ic_summary_realized=ic_summary_realized,
        ic_compare=ic_compare if not ic_compare.empty else None,
    )
    print(f"[{cfg.round_id}] 完成，耗时 {duration / 60:.1f} min")
    print(f"  报告: {report}")
    print(f"  IC summary: {out_dir / 'ic_summary.csv'}")
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="因子挖掘单轮流水线")
    ap.add_argument(
        "--config",
        default="research/config/factor_mining_smoke.yaml",
        help="YAML 配置路径",
    )
    args = ap.parse_args()
    run_round(args.config)


if __name__ == "__main__":
    main()
