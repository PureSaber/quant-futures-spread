"""Explicit offline bundle, configuration and output paths."""

import argparse
import json
from pathlib import Path

from .common import read_json
from .study import preflight, run, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description="FX, rates and credit research")
    parser.add_argument("command", choices=["preflight", "run", "verify", "demo", "import-fx"])
    parser.add_argument("--bundle")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--csv")
    parser.add_argument("--metadata")
    args = parser.parse_args(argv)
    try:
        if args.command == "import-fx":
            from .importers import import_fx

            if not args.csv or not args.metadata or not args.output:
                raise ValueError("--csv, --metadata and --output required")
            result = import_fx(args.csv, args.metadata, args.output)
        elif args.command == "verify":
            if not args.output:
                raise ValueError("--output required")
            result = verify(args.output)
        elif args.command == "demo":
            from .demo import create_demo

            if not args.output:
                raise ValueError("--output required")
            result = create_demo(args.output)
        else:
            if not args.bundle or not args.config:
                raise ValueError("--bundle and --config required")
            config = read_json(Path(args.config))
            if args.command == "preflight":
                result = preflight(args.bundle, config)
            else:
                if not args.output:
                    raise ValueError("--output required")
                result = run(args.bundle, config, args.output)
        print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        parser.exit(2, f"macro research rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
