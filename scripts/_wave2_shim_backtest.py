#!/usr/bin/env python3
"""Rewrite backtest/ as thin re-export shims onto core/ (Wave 2)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BT = ROOT / "backtest"
CORE = ROOT / "core"

SHIMS: dict[str, str] = {
    "__init__.py": "core",
    "types.py": "core.types",
    "data/__init__.py": "core.data",
    "data/bar_converter.py": "core.data.bar_converter",
    "data/dominant_calendar.py": "core.data.dominant_calendar",
    "data/market_store.py": "core.data.market_store",
    "data/spread_schedule.py": "core.data.spread_schedule",
    "data/universe.py": "core.data.universe",
    "data/roll.py": "core.data.roll",
    "engine/__init__.py": "core.engine",
    "engine/context.py": "core.engine.context",
    "engine/reconcile_sim.py": "core.engine.reconcile_sim",
    "engine/roll_governor.py": "core.engine.roll_governor",
    "engine/runner.py": "core.engine.runner",
    "engine/signal_recorder.py": "core.engine.signal_recorder",
    "io/__init__.py": "core.io",
    "io/config_loader.py": "core.io.config_loader",
    "io/output.py": "core.io.output",
    "io/output_format.py": "core.io.output_format",
    "panel/__init__.py": "core.panel",
    "panel/sector_panel.py": "core.panel.sector_panel",
    "portfolio/__init__.py": "core.portfolio",
    "portfolio/accounting.py": "core.portfolio.accounting",
    "portfolio/commission.py": "core.portfolio.commission",
    "portfolio/position_book.py": "core.portfolio.position_book",
    "portfolio/sizing.py": "core.portfolio.sizing",
}


def shim_body(target: str) -> str:
    return (
        f'"""Compatibility shim — re-exports ``{target}`` '
        f'(legacy ``backtest.*`` imports / pickle)."""\n'
        f"from {target} import *  # noqa: F403\n"
    )


def main() -> None:
    core_roll = CORE / "data" / "roll.py"
    bt_roll = BT / "data" / "roll.py"
    if bt_roll.exists() and not core_roll.exists():
        text = bt_roll.read_text(encoding="utf-8")
        if text.startswith('"""backtest/data/roll.py'):
            text = text.replace(
                '"""backtest/data/roll.py',
                '"""core/data/roll.py',
                1,
            )
        core_roll.write_text(text, encoding="utf-8")
        print(f"moved → {core_roll.relative_to(ROOT)}")

    for rel, target in SHIMS.items():
        path = BT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(shim_body(target), encoding="utf-8")
        print(f"shim  → backtest/{rel} → {target}")
    print("done")


if __name__ == "__main__":
    main()
