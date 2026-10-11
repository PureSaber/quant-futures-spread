"""Normalize authorized historical bid/ask CSV without fabricating publication times."""

import csv
import json
from pathlib import Path

from . import fx
from .common import canonical, currency, keys, nonempty, read_json, stamp
from .study import digest


def import_fx(csv_path, metadata_path, output):
    metadata = read_json(Path(metadata_path))
    keys(
        metadata,
        (
            "base",
            "quote",
            "base_rate_basis",
            "quote_rate_basis",
            "source",
            "license",
            "retrieved_at",
            "limits",
            "evidence_kind",
            "quote_use",
        ),
    )
    for field in ("source", "license", "limits"):
        nonempty(metadata[field], field)
    currency(metadata["base"])
    currency(metadata["quote"])
    stamp(metadata["retrieved_at"])
    if metadata["evidence_kind"] not in {"synthetic", "retrospective"}:
        raise ValueError("explicit synthetic/retrospective evidence_kind required")
    if metadata["quote_use"] not in {"observed_bid_ask", "reference_only"}:
        raise ValueError("explicit quote_use required")
    required = {
        "observed_at",
        "available_at",
        "bid",
        "ask",
        "base_rate",
        "quote_rate",
        "rates_available_at",
    }
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if (
            len(fields) != len(set(fields))
            or not required <= set(fields)
            or set(fields) - required - {"forwards", "fixing"}
        ):
            raise ValueError("FX CSV headers missing, duplicated or unknown")
        rows = []
        for row in reader:
            if None in row or any(row[k] in (None, "") for k in required):
                raise ValueError("malformed FX CSV row")
            result = {k: row[k] for k in required}
            if row.get("fixing"):
                result["fixing"] = row["fixing"]
            if row.get("forwards"):
                result["forwards"] = json.loads(row["forwards"])
            rows.append(result)
    raw = {k: metadata[k] for k in ("base", "quote", "base_rate_basis", "quote_rate_basis")}
    raw["quotes"] = rows
    fx.validate(raw, fx.configuration({}))
    data = canonical({"fx": raw})
    manifest = {k: metadata[k] for k in ("source", "license", "retrieved_at", "limits")}
    manifest.update(
        schema_version="quant.macro.bundle/v1",
        evidence_kind=metadata["evidence_kind"],
        data_sha256=digest(data),
        quote_use=metadata["quote_use"],
    )
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    (root / "data.json").write_bytes(data)
    (root / "manifest.json").write_bytes(canonical(manifest))
    return {
        "bundle": str(root.resolve()),
        "rows": len(rows),
        "evidence_kind": metadata["evidence_kind"],
    }


def seal(source_file, metadata_file, config_file, output):
    """Validate and seal user-authorized JSON histories before publishing a bundle."""
    from .study import calculate

    data = read_json(Path(source_file))
    meta = read_json(Path(metadata_file))
    keys(meta, ("evidence_kind", "source", "license", "retrieved_at", "quote_use", "limits"))
    if meta["evidence_kind"] not in {"synthetic", "retrospective"}:
        raise ValueError("explicit evidence_kind required")
    if meta["quote_use"] not in {"observed_bid_ask", "reference_only"}:
        raise ValueError("explicit quote_use required")
    for field in ("source", "license", "limits"):
        nonempty(meta[field], field)
    stamp(meta["retrieved_at"])
    keys(data, (), ("fx", "fixed_income", "fx_portfolio", "bond_replay", "reference"))
    payload = canonical(data)
    manifest = meta | {"schema_version": "quant.macro.bundle/v1", "data_sha256": digest(payload)}
    config, tables, ids, rows = calculate(manifest, data, read_json(Path(config_file)))
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    (root / "data.json").write_bytes(payload)
    (root / "manifest.json").write_bytes(canonical(manifest))
    return dict(
        bundle=str(root.resolve()),
        line=config["line"],
        rows=rows,
        instruments=len(ids),
        tables=list(tables),
        investable=False,
    )
