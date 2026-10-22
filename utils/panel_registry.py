"""Panels for the current backtest invocation; never reuse a disk cache.

The runner builds fresh inputs and passes that panel to its process workers.
Run IDs are display identifiers, not identities for market data or configuration.
"""
from __future__ import annotations

from typing import Any, Optional

_REGISTRY: dict[str, Any] = {}


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


def get_panel(run_id: str) -> Optional[Any]:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    if rid in _REGISTRY:
        return _migrate_panel(_REGISTRY[rid])
    return None


def discard_panel(run_id: str) -> None:
    _REGISTRY.pop(str(run_id or "").strip(), None)


def clear_panels() -> None:
    _REGISTRY.clear()


__all__ = ["register_panel", "get_panel", "discard_panel", "clear_panels"]
