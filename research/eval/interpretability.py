"""每轮挖掘后可解释性报告。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.factors.registry import FACTOR_REGISTRY, family_of


def _hint_for(factor: str) -> str:
    for f in FACTOR_REGISTRY:
        if f.name == factor:
            return f.interpret_hint
    return ""


def _narrative(factor: str, row: pd.Series, sector_ic: pd.DataFrame | None) -> str:
    ic = row.get("ic_mean", np.nan)
    icir = row.get("icir", np.nan)
    h = int(row.get("horizon", 0))
    hint = _hint_for(factor)
    direction = "正向" if ic > 0 else "负向"
    strength = "强" if abs(ic) >= 0.04 else ("中等" if abs(ic) >= 0.02 else "弱")
    parts = [
        f"**{factor}**（{family_of(factor)}）在 {h} 分钟持有期 Rank IC 均值为 {ic:.4f}（{direction}、{strength}），ICIR={icir:.2f}。",
        f"机制提示：{hint}。",
    ]
    if sector_ic is not None and not sector_ic.empty:
        sub = sector_ic[(sector_ic["factor"] == factor) & (sector_ic["horizon"] == h)]
        if not sub.empty:
            best = sub.loc[sub["rank_ic"].abs().idxmax()]
            parts.append(
                f"板块维度：{best['sector']} 上 IC={best['rank_ic']:.4f}。"
            )
    return " ".join(parts)


def generate_report(
    round_id: str,
    cfg_mode: str,
    ic_summary: pd.DataFrame,
    ic_by_sector_df: pd.DataFrame,
    quantile_results: dict[str, pd.DataFrame],
    manifest: pd.DataFrame,
    selected: pd.DataFrame,
    out_dir: Path,
    duration_sec: float,
    ic_summary_realized: pd.DataFrame | None = None,
    ic_compare: pd.DataFrame | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "interpretability_report.md"

    lines = [
        f"# 因子挖掘可解释性报告 — {round_id}",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模式：`{cfg_mode}`",
        f"- 耗时：{duration_sec / 60:.1f} 分钟",
        f"- 宇宙规模：{len(manifest)} 个 spread 文件",
        "",
        "## 1. 总体 IC 排名（按 |IC| × ICIR）",
        "",
    ]

    if not ic_summary.empty:
        top = ic_summary.copy()
        top["score"] = top["abs_ic_mean"] * top["icir"].fillna(0).abs()
        top = top.sort_values("score", ascending=False).head(20)
        lines.append("| 因子 | 持有期(min) | IC均值 | ICIR | 家族 |")
        lines.append("|------|-------------|--------|------|------|")
        for _, r in top.iterrows():
            lines.append(
                f"| {r['factor']} | {int(r['horizon'])} | {r['ic_mean']:.4f} | "
                f"{r['icir']:.2f} | {family_of(r['factor'])} |"
            )
        lines.append("")

        lines.append("## 2. 经济叙事（Top 10 因子）")
        lines.append("")
        for i, (_, r) in enumerate(top.head(10).iterrows(), 1):
            lines.append(f"{i}. {_narrative(r['factor'], r, ic_by_sector_df)}")
            lines.append("")
    else:
        lines.append("_无有效 IC 结果（样本不足或数据过滤过严）。_")
        lines.append("")

    if ic_compare is not None and not ic_compare.empty:
        lines.append("## 1b. 中间价 IC vs 可实现 IC（Top 15 |mid IC|）")
        lines.append("")
        cmp = ic_compare.copy()
        if "mid_abs_ic_mean" in cmp.columns:
            cmp = cmp.nlargest(15, "mid_abs_ic_mean")
        cols = [c for c in [
            "factor", "horizon", "mid_ic_mean", "mid_icir",
            "realized_ic_mean", "realized_icir", "ic_decay", "sign_flip",
        ] if c in cmp.columns]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for _, r in cmp.iterrows():
            cells = []
            for c in cols:
                v = r[c]
                if c == "sign_flip":
                    cells.append("Y" if v else "")
                elif isinstance(v, (float, np.floating)):
                    cells.append(f"{v:.4f}")
                else:
                    cells.append(str(v))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        lines.append(
            "> `ic_decay = realized_ic_mean - mid_ic_mean`；"
            "`sign_flip=Y` 表示扣买卖价差后 IC 符号与中间价 IC 相反。"
        )
        lines.append("")

    lines.append("## 3. 家族汇总")
    lines.append("")
    if not ic_summary.empty:
        fam = ic_summary.copy()
        fam["family"] = fam["factor"].map(family_of)
        fam_g = fam.groupby("family").agg(
            mean_abs_ic=("abs_ic_mean", "mean"),
            best_factor=("factor", lambda x: x.iloc[fam.loc[x.index, "abs_ic_mean"].argmax()]),
        ).reset_index()
        lines.append("| 家族 | 平均|IC| | 代表因子 |")
        lines.append("|------|---------|----------|")
        for _, r in fam_g.iterrows():
            lines.append(f"| {r['family']} | {r['mean_abs_ic']:.4f} | {r['best_factor']} |")
        lines.append("")

    lines.append("## 4. 分层单调性（Top 因子五分位）")
    lines.append("")
    for key, qdf in list(quantile_results.items())[:5]:
        if qdf is None or qdf.empty:
            continue
        lines.append(f"### {key}")
        lines.append("")
        lines.append("| 分位 | 平均fwd_ret | 样本数 |")
        lines.append("|------|-------------|--------|")
        for _, r in qdf.iterrows():
            lines.append(f"| {int(r['q'])} | {r['mean']:.4f} | {int(r['count'])} |")
        lines.append("")

    if not selected.empty:
        lines.append("## 5. 本轮入选因子")
        lines.append("")
        cols = list(selected.columns)
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for _, r in selected.iterrows():
            cells = [str(r[c])[:24] for c in cols]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    lines.append("## 6. 风险与局限")
    lines.append("")
    lines.append("- 中间价 IC 基于 `fwd_ret_*`；可实现 IC 基于 `fwd_realized_long_*`（买 ask / 卖 bid）。")
    lines.append("- `stationarity` 仅部分 2024 文件有值；`coint_gate` 覆盖率受限。")
    lines.append("- 截面因子 `sector_rank` 在单 spread 流程中为日频聚合补充。")
    lines.append("- 建议 Round 2 对入选因子做 2025 OOS 与参数敏感性检验。")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
