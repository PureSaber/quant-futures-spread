"""Additive CLI: python -m qfs_global.research --help."""

import argparse
import json
from pathlib import Path

from quant_data_kit.derivatives import load_bundle
from quant_data_kit.derivatives.bundle import digest, json_bytes
from quant_execution.derivative_replay import write_artifacts

from .basis import analyze_basis
from .coverage import coverage_report, merge_bundles
from .free_reference import collect_references
from .portfolio import run_portfolio
from .universe import universe


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Global futures universe, coverage, basis and portfolios"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("universe")
    reference = sub.add_parser("free-reference")
    reference.add_argument("--symbols", required=True)
    reference.add_argument("--start", required=True)
    reference.add_argument("--end", required=True)
    reference.add_argument("--output", required=True)
    merge = sub.add_parser("merge-history")
    merge.add_argument("--bundle", action="append", required=True)
    merge.add_argument("--output", required=True)
    merge.add_argument("--reconcile-first-observed", action="store_true")
    audit = sub.add_parser("coverage")
    audit.add_argument("--bundle", action="append", default=[])
    audit.add_argument("--requirements", required=True)
    audit.add_argument("--output", required=True)
    basis = sub.add_parser("basis")
    basis.add_argument("--observations", required=True)
    portfolio = sub.add_parser("portfolio")
    portfolio.add_argument("--config", required=True)
    for p in (basis, portfolio):
        p.add_argument("--bundle", required=True)
        p.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "universe":
            result = universe()
        elif args.command == "free-reference":
            result = collect_references(args.output, args.symbols, args.start, args.end)
        elif args.command == "merge-history":
            result = merge_bundles(
                args.bundle, args.output, reconcile_first_observed=args.reconcile_first_observed
            ).summary()
        elif args.command == "coverage":
            result = coverage_report([load_bundle(b) for b in args.bundle], read(args.requirements))
            # Exclusive create keeps evidence immutable without overwriting prior audits.
            with Path(args.output).open("x", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
        elif args.command == "portfolio":
            result = run_portfolio(args.bundle, read(args.config), args.output)
        else:
            bundle = load_bundle(args.bundle)
            observations = read(args.observations)
            rows = analyze_basis(bundle, observations)
            result = {
                "title": "股指期货基差与期现联动研究",
                "evidence_kind": bundle.manifest["evidence_kind"],
                "input_sha256": digest(
                    json_bytes({"bundle": bundle.identity, "spot_carry": observations})
                ),
                "observations": len(rows),
                "limits": "ACT/365F continuous funding minus dividend-yield carry. Requires synchronized matching spot identity and currency. Basis residual is not executable arbitrage profit; dividends, financing, stock-basket execution and shorting costs need independent validation.",
            }
            write_artifacts(args.output, result, {"basis.csv": rows})
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        parser.exit(2, f"global futures research rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
