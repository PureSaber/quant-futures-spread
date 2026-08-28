"""Fixture-certified domestic-futures backtests backed only by frozen public contracts."""

from qfs_certified.reference import FixtureMaster, load_fixture_master
from qfs_certified.runner import CertifiedRun, run_certified_backtest
from qfs_certified.strategy import AuditedSpreadStrategy, LegIntentAudit, SpreadSignal

__all__ = [
    "AuditedSpreadStrategy",
    "CertifiedRun",
    "FixtureMaster",
    "LegIntentAudit",
    "SpreadSignal",
    "load_fixture_master",
    "run_certified_backtest",
]
