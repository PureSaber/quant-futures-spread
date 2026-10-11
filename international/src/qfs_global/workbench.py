"""Read-only preflight and report adapters for the research workbench."""

import argparse
import json
from pathlib import Path

from quant_data_kit.derivatives import load_bundle
from quant_data_kit.derivatives.bundle import digest, json_bytes
from quant_execution.derivative_replay import replay, verify_artifacts, write_artifacts

from .basis import analyze_basis
from .portfolio import portfolio_selector, run_portfolio


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "run"])
    parser.add_argument("--kind", choices=["portfolio", "basis", "reference"], required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        root = Path(args.bundle)
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if args.kind == "reference":
            verify_artifacts(root)
            study = json.loads((root / "study.json").read_text(encoding="utf-8"))
            if study.get("schema") != "qfs.vendor-reference/v1":
                raise ValueError("vendor reference artifact schema required")
            import csv

            with (root / "reference_prices.csv").open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            if not rows:
                raise ValueError("empty reference history")
            result = {
                "evidence_kind": study["evidence_kind"],
                "rows": len(rows),
                "instrument_ids": [r["symbol"] for r in study["coverage"]],
            }
            if args.command == "run":
                if not args.output:
                    raise ValueError("--output required")
                write_artifacts(args.output, study, {"reference_prices.csv": rows})
        else:
            bundle = load_bundle(root)
            if args.kind == "portfolio":
                account, decide = portfolio_selector(bundle, config)
                result = bundle.summary()
                if args.command == "preflight":
                    replay(bundle, account, decide)  # validates the complete read-only path
                else:
                    if not args.output:
                        raise ValueError("--output required")
                    result = run_portfolio(root, config, args.output)
            else:
                if set(config) != {"observations"}:
                    raise ValueError("basis config requires observations only")
                rows = analyze_basis(bundle, config["observations"])
                result = bundle.summary()
                if args.command == "run":
                    if not args.output:
                        raise ValueError("--output required")
                    study = {
                        "title": "股指期货基差与期现联动",
                        "evidence_kind": result["evidence_kind"],
                        "input_sha256": digest(
                            json_bytes({"bundle": bundle.identity, "config": config})
                        ),
                        "rows": len(rows),
                        "limits": "Synchronized observations and declared carry; no executable arbitrage profit claim",
                    }
                    write_artifacts(args.output, study, {"basis.csv": rows})
        if args.command == "preflight":
            result.update(
                schema_version="qdk.derivatives.preflight/v1",
                software_preflight="pass",
                read_only=True,
                investable=False,
            )
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        parser.exit(2, f"workbench research rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
