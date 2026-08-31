"""QLab standard/v2 adapter sourced exclusively from QExec run facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from quant_data_kit import BarEvent, FixedPoint, MarkPriceEvent, MarketEvent, QuoteEvent, TradeEvent
from quant_execution import AccountSnapshot, RunArtifacts
from quant_lab import load_and_validate_standard_run, write_standard_run_v2
from quant_lab.contracts_v2 import ARTIFACT_SCHEMAS_V2, RunManifestV2

from qfs_certified.reference import FixtureMaster

INTERNAL_DEPENDENCIES = {
    "quant-data-kit": "v0.8.1",
    "quant-execution": "v0.5.1",
    "quant-lab": "v0.3.1",
}


def _frame(name: str, rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    result = pd.DataFrame(rows, columns=ARTIFACT_SCHEMAS_V2[name])
    if not result.empty:
        sort_columns = ["event_time"]
        for candidate in ("order_id", "event_sequence", "transaction_id", "posting_index"):
            if candidate in result.columns:
                sort_columns.append(candidate)
        result = result.sort_values(sort_columns, kind="stable").reset_index(drop=True)
    return result


def _money(value: Decimal, scale: int) -> FixedPoint:
    return FixedPoint.from_decimal(value, scale, rounding=ROUND_HALF_EVEN)


def _sum_fixed(values) -> Decimal:
    return sum((item.to_decimal() for item in values), Decimal(0))


def _event_price(event: MarketEvent) -> FixedPoint | None:
    if isinstance(event, (MarkPriceEvent, TradeEvent)):
        return event.price
    if isinstance(event, BarEvent):
        return event.close_price
    if isinstance(event, QuoteEvent):
        midpoint = (event.bid_price.to_decimal() + event.ask_price.to_decimal()) / 2
        return FixedPoint.from_decimal(midpoint, event.bid_price.scale)
    return None


def _marks_at_snapshots(
    events: Sequence[MarketEvent], snapshots: Sequence[AccountSnapshot]
) -> dict[datetime, dict[str, FixedPoint]]:
    ordered_events = sorted(events, key=lambda item: (item.available_at, item.event_id))
    marks: dict[str, FixedPoint] = {}
    by_time: dict[datetime, dict[str, FixedPoint]] = {}
    cursor = 0
    for snapshot in snapshots:
        while (
            cursor < len(ordered_events)
            and ordered_events[cursor].available_at <= snapshot.event_time
        ):
            event = ordered_events[cursor]
            price = _event_price(event)
            if price is not None:
                marks[event.instrument_id] = price
            cursor += 1
        by_time[snapshot.event_time] = dict(marks)
    return by_time


def _reporting_frames(
    *,
    artifacts: RunArtifacts,
    snapshots: Sequence[AccountSnapshot],
    master: FixtureMaster,
    strategy_id: str,
    money_scale: int,
) -> dict[str, pd.DataFrame]:
    marks_by_time = _marks_at_snapshots(artifacts.market_events, snapshots)
    fee_by_time: dict[datetime, Decimal] = {}
    for fee in artifacts.fees:
        fee_by_time[fee.event_time] = fee_by_time.get(fee.event_time, Decimal(0)) + Decimal(
            fee.amount.to_decimal()
        )

    returns: list[dict] = []
    positions: list[dict] = []
    portfolio: list[dict] = []
    exposures: list[dict] = []
    margins: list[dict] = []
    prior_nav: Decimal | None = None
    for snapshot in snapshots:
        if set(snapshot.cash_balances) - {snapshot.base_currency}:
            raise ValueError("certified fixture reporting requires base-currency-only cash")
        nav = snapshot.nav.to_decimal()
        if prior_nav is None:
            net_return = Decimal(0)
            gross_return = Decimal(0)
        else:
            if prior_nav == 0:
                raise ValueError("cannot calculate return from zero NAV")
            net_return = (nav - prior_nav) / prior_nav
            gross_return = (
                nav + fee_by_time.get(snapshot.event_time, Decimal(0)) - prior_nav
            ) / prior_nav
        returns.append(
            {
                "event_time": snapshot.event_time,
                "strategy_id": strategy_id,
                "gross_return": float(gross_return),
                "net_return": float(net_return),
                "nav_units": snapshot.nav.units,
                "nav_scale": snapshot.nav.scale,
                "base_currency": snapshot.base_currency,
            }
        )
        prior_nav = nav
        cash_value = _sum_fixed(snapshot.cash_balances.values())
        unrealized = _sum_fixed(snapshot.unrealized_pnl.values())
        realized = _sum_fixed(snapshot.realized_pnl.values())
        market_value = nav - cash_value
        portfolio.append(
            {
                "event_time": snapshot.event_time,
                "account_id": snapshot.account_id,
                "base_currency": snapshot.base_currency,
                "nav_units": snapshot.nav.units,
                "nav_scale": snapshot.nav.scale,
                "cash_value_units": _money(cash_value, money_scale).units,
                "cash_value_scale": money_scale,
                "market_value_units": _money(market_value, money_scale).units,
                "market_value_scale": money_scale,
                "unrealized_pnl_units": _money(unrealized, money_scale).units,
                "unrealized_pnl_scale": money_scale,
                "realized_pnl_units": _money(realized, money_scale).units,
                "realized_pnl_scale": money_scale,
                "margin_used_units": snapshot.initial_margin.units,
                "margin_used_scale": snapshot.initial_margin.scale,
            }
        )
        marks = marks_by_time[snapshot.event_time]
        decomposed_initial = Decimal(0)
        decomposed_maintenance = Decimal(0)
        for instrument_id, quantity in snapshot.positions.items():
            if quantity.units == 0:
                continue
            spec = master.instruments[instrument_id]
            if spec.settlement_currency != snapshot.base_currency:
                raise ValueError(
                    "certified fixture reporting requires instrument settlement currency "
                    "to equal the QExec account base currency"
                )
            try:
                mark = marks[instrument_id]
            except KeyError as exc:
                raise ValueError(f"missing PIT mark for position {instrument_id}") from exc
            notional = (
                quantity.to_decimal() * mark.to_decimal() * spec.contract_multiplier.to_decimal()
            )
            base_notional = _money(notional, money_scale)
            positions.append(
                {
                    "event_time": snapshot.event_time,
                    "account_id": snapshot.account_id,
                    "strategy_id": strategy_id,
                    "instrument_id": instrument_id,
                    "quantity_units": quantity.units,
                    "quantity_scale": quantity.scale,
                    "mark_price_units": mark.units,
                    "mark_price_scale": mark.scale,
                    "market_value_units": base_notional.units,
                    "market_value_scale": base_notional.scale,
                    "currency": spec.settlement_currency,
                    "fx_rate_units": 1,
                    "fx_rate_scale": 0,
                    "fx_snapshot_id": f"fx:{snapshot.base_currency}:base",
                    "base_market_value_units": base_notional.units,
                    "base_market_value_scale": base_notional.scale,
                }
            )
            exposures.append(
                {
                    "event_time": snapshot.event_time,
                    "account_id": snapshot.account_id,
                    "strategy_id": strategy_id,
                    "exposure_type": "signed_notional",
                    "name": instrument_id,
                    "value": float(notional),
                    "unit": spec.settlement_currency,
                }
            )
            initial = abs(notional) * Decimal(spec.metadata["initial_margin_rate"])
            maintenance = abs(notional) * Decimal(spec.metadata["maintenance_margin_rate"])
            reported_initial = _money(initial, money_scale)
            reported_maintenance = _money(maintenance, money_scale)
            decomposed_initial += reported_initial.to_decimal()
            decomposed_maintenance += reported_maintenance.to_decimal()
            margins.append(
                {
                    "event_time": snapshot.event_time,
                    "account_id": snapshot.account_id,
                    "instrument_id": instrument_id,
                    "initial_margin_units": reported_initial.units,
                    "maintenance_margin_units": reported_maintenance.units,
                    "margin_scale": money_scale,
                    "currency": snapshot.base_currency,
                }
            )
        if _money(decomposed_initial, money_scale) != snapshot.initial_margin:
            raise ValueError(
                "reporting margin decomposition differs from QExec aggregate initial margin"
            )
        if _money(decomposed_maintenance, money_scale) != snapshot.maintenance_margin:
            raise ValueError(
                "reporting margin decomposition differs from QExec aggregate maintenance margin"
            )

    orders = [
        {
            "event_time": order.intent.created_at,
            "order_id": order.order_id,
            "idempotency_key": order.intent.idempotency_key,
            "account_id": order.intent.account_id,
            "strategy_id": order.intent.strategy_id,
            "instrument_id": order.intent.instrument_id,
            "side": order.intent.side.value,
            "quantity_units": order.intent.quantity.units,
            "quantity_scale": order.intent.quantity.scale,
            "order_type": order.intent.order_type.value,
            "limit_price_units": order.intent.limit_price.units
            if order.intent.limit_price
            else None,
            "limit_price_scale": order.intent.limit_price.scale
            if order.intent.limit_price
            else None,
            "stop_price_units": order.intent.stop_price.units if order.intent.stop_price else None,
            "stop_price_scale": order.intent.stop_price.scale if order.intent.stop_price else None,
            "time_in_force": order.intent.time_in_force.value,
            "reduce_only": order.intent.reduce_only,
            "status": order.status.value,
            "filled_quantity_units": order.filled_quantity.units,
            "filled_quantity_scale": order.filled_quantity.scale,
            "version": order.version,
        }
        for order in artifacts.orders
    ]
    order_events = [
        {
            "event_time": event.event_time,
            "event_id": event.event_id,
            "order_id": event.order_id,
            "event_sequence": event.sequence,
            "from_status": event.from_status.value,
            "to_status": event.to_status.value,
            "fill_quantity_units": event.fill_quantity.units if event.fill_quantity else None,
            "fill_quantity_scale": event.fill_quantity.scale if event.fill_quantity else None,
            "reason": event.reason,
        }
        for event in artifacts.order_events
    ]
    fills = [
        {
            "event_time": fill.event_time,
            "fill_id": fill.fill_id,
            "order_id": fill.order_id,
            "account_id": fill.account_id,
            "strategy_id": fill.strategy_id,
            "instrument_id": fill.instrument_id,
            "side": fill.side.value,
            "quantity_units": fill.quantity.units,
            "quantity_scale": fill.quantity.scale,
            "price_units": fill.price.units,
            "price_scale": fill.price.scale,
            "currency": master.instruments[fill.instrument_id].settlement_currency,
            "liquidity_role": fill.liquidity_role.value,
            "venue_trade_id": fill.venue_trade_id,
        }
        for fill in artifacts.fills
    ]
    fill_by_id = {fill.fill_id: fill for fill in artifacts.fills}
    costs = []
    for fee in artifacts.fees:
        fill = fill_by_id[fee.fill_id]
        costs.append(
            {
                "event_time": fee.event_time,
                "cost_id": fee.fee_id,
                "account_id": fee.account_id,
                "strategy_id": fill.strategy_id,
                "instrument_id": fill.instrument_id,
                "fill_id": fee.fill_id,
                "cost_type": fee.fee_type,
                "amount_units": fee.amount.units,
                "amount_scale": fee.amount.scale,
                "currency": fee.currency,
            }
        )
    cash_ledger = []
    for transaction in artifacts.ledger_transactions:
        for index, posting in enumerate(transaction.postings):
            cash_ledger.append(
                {
                    "event_time": transaction.event_time,
                    "transaction_id": transaction.transaction_id,
                    "idempotency_key": transaction.idempotency_key,
                    "event_type": transaction.event_type.value,
                    "reference_id": transaction.reference_id,
                    "posting_index": index,
                    "ledger_account": posting.ledger_account,
                    "account_id": artifacts.orders[0].intent.account_id,
                    "currency": posting.currency,
                    "amount_units": posting.amount.units,
                    "amount_scale": posting.amount.scale,
                    "instrument_id": posting.instrument_id,
                    "quantity_delta_units": (
                        posting.quantity_delta.units if posting.quantity_delta else None
                    ),
                    "quantity_delta_scale": (
                        posting.quantity_delta.scale if posting.quantity_delta else None
                    ),
                }
            )
    return {
        "returns": _frame("returns", returns),
        "positions": _frame("positions", positions),
        "portfolio_snapshots": _frame("portfolio_snapshots", portfolio),
        "exposures": _frame("exposures", exposures),
        "orders": _frame("orders", orders),
        "order_events": _frame("order_events", order_events),
        "fills": _frame("fills", fills),
        "costs": _frame("costs", costs),
        "cash_ledger": _frame("cash_ledger", cash_ledger),
        "margin": _frame("margin", margins),
    }


def _lineage() -> dict[str, list[str]]:
    return {
        "config": [],
        "metrics": ["portfolio_snapshots", "costs"],
        "returns": ["portfolio_snapshots", "costs"],
        "positions": ["dataset:market_events", "dataset:instrument_master"],
        "portfolio_snapshots": ["cash_ledger", "dataset:market_events"],
        "exposures": ["positions"],
        "orders": ["config", "dataset:signal_plan", "dataset:market_events"],
        "order_events": ["orders"],
        "fills": ["order_events", "dataset:market_events"],
        "costs": ["fills"],
        "cash_ledger": ["fills", "costs", "config"],
        "margin": ["positions", "portfolio_snapshots"],
    }


def write_certified_standard_v2(
    run_dir: Path,
    *,
    artifacts: RunArtifacts,
    snapshots: Sequence[AccountSnapshot],
    master: FixtureMaster,
    strategy_id: str,
    config: Mapping[str, Any],
    code_version: str,
    dataset_snapshots: Mapping[str, str],
    instrument_master_version: str,
    random_seed: int,
    created_at: str,
    money_scale: int,
) -> RunManifestV2:
    frames = _reporting_frames(
        artifacts=artifacts,
        snapshots=snapshots,
        master=master,
        strategy_id=strategy_id,
        money_scale=money_scale,
    )
    final_snapshot = snapshots[-1]
    metrics = {
        "event_count": artifacts.result.event_count,
        "order_count": artifacts.result.order_count,
        "fill_count": artifacts.result.fill_count,
        "settlement_count": len(artifacts.settlements),
        "final_nav": str(final_snapshot.nav.to_decimal()),
        "order_event_sha256": artifacts.result.event_sha256,
        "fill_sha256": artifacts.result.fill_sha256,
        "ledger_sha256": artifacts.result.ledger_sha256,
    }
    manifest = write_standard_run_v2(
        Path(run_dir),
        project="quant-futures-spread",
        run_id=artifacts.result.run_id,
        strategy_ids=[strategy_id],
        profile="backtest-ledger",
        frames=frames,
        metrics=metrics,
        config=dict(config),
        code_version=code_version,
        internal_dependencies=INTERNAL_DEPENDENCIES,
        random_seed=random_seed,
        dataset_snapshots=dataset_snapshots,
        instrument_master_version=instrument_master_version,
        execution_model_version="quant-execution-v0.5.1:bar-matching",
        base_currency=final_snapshot.base_currency,
        lineage=_lineage(),
        capabilities=[
            "deterministic",
            "fixture-certified",
            "leg-level-orders",
            "no-live-path",
            "pit-reference-data",
        ],
        tags={
            "asset_class": "cn_commodity_futures",
            "accounting_source": "quant-execution-v0.5.1",
            "certification": "fixture-certified",
            "legacy_accounting": "excluded",
        },
        created_at=created_at,
    )
    loaded = load_and_validate_standard_run(Path(run_dir))
    if loaded != manifest:
        raise ValueError("standard/v2 read-back differs from the written manifest")
    return manifest
