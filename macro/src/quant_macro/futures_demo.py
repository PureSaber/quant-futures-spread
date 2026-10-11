"""Exchange-unit fixtures; fictional expiries, prices and margins, NOT real contracts."""

from dataclasses import replace
from decimal import Decimal

from .futures_cli import RULES


def records(product):
    from quant_data_kit.derivatives.demo import demo_records

    rule = RULES[product]
    contracts, quotes = demo_records("future")
    tick = Decimal(rule["ticks"][0])
    renamed = {
        c.instrument_id: c.instrument_id.replace("DEMO", "SYNTH-" + product) for c in contracts
    }
    converted = [
        replace(
            c,
            instrument_id=renamed[c.instrument_id],
            symbol="SYNTH-" + product + c.symbol[-1],
            product=product,
            venue=rule["venue"],
            currency=rule["currency"],
            multiplier=Decimal(rule["multiplier"]),
            tick=tick,
            settlement=rule["settlement"],
            initial_margin=Decimal(5000),
            maintenance_margin=Decimal(4000),
            rules_source=rule["source"] + "#synthetic-expiry-margin-fixture",
        )
        for c in contracts
    ]
    result = []
    for q in quotes:
        # Exact tick lattice; fixture price level only, no real observations implied.
        px = (
            Decimal("1.1") if product == "6E" else Decimal(96) if product == "SR3" else Decimal(112)
        )
        px += (q.close - 100) * tick * 10
        px = (px / tick).to_integral_value() * tick
        result.append(
            replace(
                q,
                instrument_id=renamed[q.instrument_id],
                open=px - tick,
                high=px + 2 * tick,
                low=px - 2 * tick,
                close=px,
                settlement=px,
                bid=px - tick,
                ask=px + tick,
            )
        )
    return converted, result


def create(output, product):
    from quant_data_kit.derivatives import write_bundle

    contracts, quotes = records(product)
    return write_bundle(
        output,
        contracts,
        quotes,
        provider="macro-product-fixture",
        evidence_kind="synthetic",
        rights_note="Locally generated synthetic data",
        limits="Product price units only. Dates, quotes, margins and liquidity fictional; "
        "no exchange historical certification.",
    )
