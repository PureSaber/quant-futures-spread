"""Explicit offline bundle, configuration and output paths."""

import argparse
import json
from pathlib import Path

from .common import read_json
from .study import preflight, run, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description="FX, rates and credit research")
    parser.add_argument(
        "command",
        choices=[
            "preflight",
            "run",
            "verify",
            "demo",
            "import-fx",
            "import-public",
            "download-public",
            "seal",
        ],
    )
    parser.add_argument("--bundle")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--csv")
    parser.add_argument("--metadata")
    parser.add_argument("--provider", choices=["ecb", "fed-gsw", "lqd-holdings"])
    parser.add_argument("--source-file")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end")
    parser.add_argument("--retrieved-at")
    args = parser.parse_args(argv)
    try:
        if args.command in {"import-public", "download-public"}:
            from .public_data import download_public, import_public

            if not args.provider or not args.output:
                raise ValueError("--provider and --output required")
            options = dict(start=args.start, end=args.end, retrieved_at=args.retrieved_at)
            if args.command == "import-public":
                if not args.source_file:
                    raise ValueError("--source-file required")
                result = import_public(args.provider, args.source_file, args.output, **options)
            else:
                result = download_public(args.provider, args.output, **options)
        elif args.command == "seal":
            from .importers import seal

            if not all((args.source_file, args.metadata, args.config, args.output)):
                raise ValueError("--source-file, --metadata, --config and --output required")
            result = seal(args.source_file, args.metadata, args.config, args.output)
        elif args.command == "import-fx":
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
