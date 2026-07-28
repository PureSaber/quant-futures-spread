"""utils/strategy_bootstrap.py —— 注入 strategy/ 路径（优先于 repo 根）。"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STRATEGY_ROOT = _REPO_ROOT / "strategy"


def bootstrap_strategy_path() -> str:
    """注入 strategy/ 路径，幂等。strategy/ 优先于 repo 根，保证 from framework / from strategies 命中可移植层。"""
    repo = str(_REPO_ROOT)
    strat = str(_STRATEGY_ROOT)
    if strat in sys.path:
        sys.path.remove(strat)
    sys.path.insert(0, strat)
    if repo not in sys.path:
        sys.path.insert(1, repo)
    return strat
