"""performance/report/generate.py —— 生成 performance_report_{run_id}.xlsx。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPORT_ROOT = Path(__file__).parent
if str(_REPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPORT_ROOT))

from loader import load_config, prepare_frames  # noqa: E402
from src.excel_formatter import write_excel  # noqa: E402
from src.reports import annual, core_overview, monthly, weekly  # noqa: E402

_SYMBOL_LABEL = "套利对"


def _rename_symbol_col(df: pd.DataFrame) -> pd.DataFrame:
    if "品种" in df.columns:
        return df.rename(columns={"品种": _SYMBOL_LABEL})
    return df


def generate_report(
    spread_daily: pd.DataFrame,
    portfolio_daily: pd.DataFrame,
    fills: pd.DataFrame | None,
    output_path: str | Path,
    capital: float,
    strategy: str = "",
    config_path: Path | None = None,
    total_capital: float | None = None,
) -> Path:
    """生成 7 张 sheet 的 Excel 绩效报告（对齐 cta_strategy_analysis）。

    capital: 单实例本金（symbol / trades 口径）。
    total_capital: 组合总本金（=活跃实例数×单实例本金），portfolio 口径；默认=capital。
    """
    total_cap = float(total_capital) if total_capital is not None else float(capital)
    config = load_config(config_path)
    config = {**config, "per_strategy_capital": total_cap}

    portfolio, symbol, trades = prepare_frames(
        spread_daily, portfolio_daily, fills, capital, strategy,
        port_capital=total_cap,
    )

    sheets: dict[str, pd.DataFrame] = {
        "核心概览": core_overview.build(portfolio, trades, config),
        "年度_组合": annual.build_portfolio(portfolio, symbol, trades, config),
        "年度_品种": _rename_symbol_col(
            annual.build_symbol(symbol, trades, config),
        ),
        "月度_组合": monthly.build_portfolio(portfolio, symbol, trades, config),
        "月度_品种": _rename_symbol_col(
            monthly.build_symbol(symbol, trades, config),
        ),
        "周度_组合": weekly.build_portfolio(portfolio, symbol, trades, config),
        "周度_品种": _rename_symbol_col(
            weekly.build_symbol(symbol, trades, config),
        ),
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_excel(sheets, out)
    return out
