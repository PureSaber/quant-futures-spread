import argparse
import json
from pathlib import Path

from quant_data_kit.derivatives import load_bundle
from quant_execution.derivative_replay import verify_artifacts

from .study import preflight, run


def main(argv=None):
    parser = argparse.ArgumentParser(description="International futures research")
    parser.add_argument("command", choices=["preflight", "run", "verify"])
    parser.add_argument("--bundle")
    parser.add_argument("--config")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            if not args.output:
                raise ValueError("--output required for verify")
            print(json.dumps(verify_artifacts(args.output), indent=2))
            return 0
        if not args.bundle or not args.config:
            raise ValueError("--bundle and --config required")
        config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
        if args.command == "preflight":
            result = preflight(load_bundle(args.bundle), config)
        else:
            if not args.output:
                raise ValueError("--output required")
            result = run(args.bundle, config, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"futures research rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
