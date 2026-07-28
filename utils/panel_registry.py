"""utils/panel_registry.py —— 回测截面 panel 注册表（策略只读此模块）。"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Optional

_REGISTRY: dict[str, Any] = {}
_CACHE_DIR = Path(__file__).resolve().parent.parent / "output" / ".panel_cache"


def _migrate_panel(panel: Any) -> Any:
    if panel is not None and not hasattr(panel, "calendar_by_product"):
        panel.calendar_by_product = {}
    return panel


def register_panel(run_id: str, panel: Any) -> None:
    rid = str(run_id or "").strip()
    if not rid:
        return
    panel = _migrate_panel(panel)
    _REGISTRY[rid] = panel
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with (_CACHE_DIR / f"{rid}.pkl").open("wb") as f:
        pickle.dump(panel, f, protocol=pickle.HIGHEST_PROTOCOL)


def _ensure_pickle_compat() -> None:
    """旧 panel pickle 可能引用 ``backtest.*`` 模块路径。"""
    import sys
    import types

    from core.panel import sector_panel

    if "backtest" not in sys.modules:
        bt = types.ModuleType("backtest")
        bt.panel = types.ModuleType("backtest.panel")
        bt.panel.sector_panel = sector_panel
        bt.sector_panel = sector_panel
        sys.modules["backtest"] = bt
        sys.modules["backtest.panel"] = bt.panel
    sys.modules.setdefault("backtest.sector_panel", sector_panel)
    sys.modules.setdefault("backtest.panel.sector_panel", sector_panel)
    sys.modules.setdefault("core.sector_panel", sector_panel)


def get_panel(run_id: str) -> Optional[Any]:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    if rid in _REGISTRY:
        return _migrate_panel(_REGISTRY[rid])
    path = _CACHE_DIR / f"{rid}.pkl"
    if path.is_file():
        _ensure_pickle_compat()
        with path.open("rb") as f:
            panel = pickle.load(f)
        panel = _migrate_panel(panel)
        _REGISTRY[rid] = panel
        return panel
    return None


def clear_panels() -> None:
    _REGISTRY.clear()


__all__ = ["register_panel", "get_panel", "clear_panels"]
