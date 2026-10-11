"""Product rule gate around the existing QDK/QExec futures research engine.

Only unadjusted, normalized decimal prices are accepted. Expiries, historical
margins and rule vintages remain explicit bundle inputs. No live delivery.
"""

import argparse
import json
import re
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from .common import read_json

RULES = {
    "6E": dict(
        venue="CME",
        currency="USD",
        multiplier="125000",
        ticks=["0.00005"],
        settlement="physical",
        quotation="USD per EUR",
        source="https://www.cmegroup.com/markets/fx/g10/euro-fx/specs",
    ),
    "ZN": dict(
        venue="CBOT",
        currency="USD",
        multiplier="1000",
        ticks=["0.015625"],
        settlement="physical",
        quotation="decimal points per 100 face",
        source="https://www.cmegroup.com/trading/interest-rates/basics-of-us-treasury-futures.html",
    ),
    "SR3": dict(
        venue="CME",
        currency="USD",
        multiplier="2500",
        ticks=["0.0025", "0.005"],
        settlement="cash",
        quotation="100 minus annual rate in percent",
        source="https://www.cmegroup.com/education/articles-and-reports/understanding-sofr-futures",
    ),
}
for _product, _multiplier, _ticks, _source in [
    ("TS", "20000", ["0.002", "0.005"], "https://www.cffex.com.cn/jystz/20231020/34772.html"),
    ("TF", "10000", ["0.005"], "https://www.e-cffex.com.cn/u/cms/www/202007/08152340rnz2.pdf"),
    ("T", "10000", ["0.005"], "https://www.e-cffex.com.cn/u/cms/www/202007/231658479i3m.pdf"),
    (
        "TL",
        "10000",
        ["0.01"],
        "https://oa.hicend.com.cn/ftpfile/static/file/upload/file/202304/17/mNwEiYcnmdxTFCmL.pdf",
    ),
]:
    RULES[_product] = dict(
        venue="CFFEX",
        currency="CNY",
        multiplier=_multiplier,
        ticks=_ticks,
        settlement="physical",
        quotation="CNY clean points per 100 face",
        source=_source,
    )


def treasury_decimal(value):
    """Explicit CBOT notation such as 112-165 -> 112 + 16.5/32."""
    if not isinstance(value, str) or not re.fullmatch(r"\d+-\d{2}[05]?", value):
        raise ValueError("expected points-32nds[0|5] treasury notation")
    whole, fraction = value.split("-")
    numerator = Decimal(fraction[:2])
    if numerator >= 32:
        raise ValueError("32nds outside range")
    if len(fraction) == 3:
        numerator += Decimal(fraction[2]) / 10
    return Decimal(whole) + numerator / 32


def validate_rules(bundle, config):
    from qfs_global.study import preflight

    result = preflight(bundle, config)
    product = config.get("product")
    if product not in RULES:
        raise ValueError("supported macro futures products: " + ", ".join(RULES))
    rule = RULES[product]
    selected = {c.instrument_id: c for c in bundle.contracts if c.product == product}
    for contract in selected.values():
        for key in ("venue", "currency", "settlement"):
            if getattr(contract, key) != rule[key]:
                raise ValueError(f"{product} {key} disagrees with rule profile")
        if contract.multiplier != Decimal(rule["multiplier"]):
            raise ValueError("incorrect price-point multiplier")
        if contract.tick not in {Decimal(x) for x in rule["ticks"]}:
            raise ValueError("incorrect outright tick")
        if not contract.rules_source.startswith("https://"):
            raise ValueError("historical contract rule source URL required")
        quotes = [q for q in bundle.quotes if q.instrument_id == contract.instrument_id]
        if any(contract.known_at > q.available_at for q in quotes):
            raise ValueError("future-known contract specification")
        for quote in quotes:
            if product == "TS":
                expected = Decimal("0.002" if quote.session >= "2023-11-07" else "0.005")
                if contract.tick != expected:
                    raise ValueError("TS tick regime mismatch; split rule versions at 2023-11-07")
            for field in ("open", "high", "low", "close", "bid", "ask"):
                value = getattr(quote, field)
                if value is not None and value % contract.tick:
                    raise ValueError("off-tick outright price; normalize fractional notation first")
            # Settlement may use a finer precision than executable ticks (e.g. SR3 final).
            if quote.at > contract.last_trade_at:
                raise ValueError("post-last-trade observations cannot be traded in this adapter")
    return result | {
        "rule_profile": product,
        "quotation": rule["quotation"],
        "rule_source": rule["source"],
        "historical_rules_certified": False,
        "rules_snapshot": "2026-10-11",
        "margin_source": "explicit per-contract bundle terms",
    }


def run(bundle_path, config, output):
    from qfs_global.study import run as run_global
    from quant_data_kit.derivatives import load_bundle

    # Freeze the exact validated bytes so changes to a shared data path cannot bypass the gate.
    with tempfile.TemporaryDirectory(prefix="macro-futures-") as temp:
        frozen = Path(temp)
        for name in ("manifest.json", "contracts.json", "quotes.csv"):
            original = Path(bundle_path) / name
            if original.is_symlink() or not original.is_file():
                raise ValueError("regular bundle files required")
            shutil.copyfile(original, frozen / name)
        bundle = load_bundle(frozen)
        check = validate_rules(bundle, config)
        study = run_global(frozen, config, output)
        return study | {"macro_rule_validation": check}


def main(argv=None):
    parser = argparse.ArgumentParser(description="FX, Treasury and STIR contract rule research")
    parser.add_argument("command", choices=["preflight", "run", "verify"])
    parser.add_argument("--bundle")
    parser.add_argument("--config")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            from quant_execution.derivative_replay import verify_artifacts

            if not args.output:
                raise ValueError("--output required")
            result = verify_artifacts(args.output)
        else:
            from quant_data_kit.derivatives import load_bundle

            if not args.bundle or not args.config:
                raise ValueError("--bundle and --config required")
            config = read_json(Path(args.config))
            if args.command == "preflight":
                result = validate_rules(load_bundle(args.bundle), config)
            else:
                if not args.output:
                    raise ValueError("--output required")
                result = run(args.bundle, config, args.output)
        print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"macro futures rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
