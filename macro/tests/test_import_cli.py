import csv
import json

import pytest

from quant_macro.cli import main
from quant_macro.common import canonical
from quant_macro.demo import demo_data
from quant_macro.importers import import_fx
from quant_macro.study import load_bundle, preflight


def write_csv(tmp_path):
    data = demo_data()["fx"]
    path = tmp_path / "quotes.csv"
    rows = [dict(row) for row in data["quotes"]]
    for row in rows:
        row["forwards"] = json.dumps(row["forwards"])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metadata = {k: v for k, v in data.items() if k != "quotes"}
    metadata.update(
        source="synthetic CSV regression fixture",
        license="fixture",
        retrieved_at="2026-01-14T00:00:00+00:00",
        limits="synthetic only",
        evidence_kind="synthetic",
        quote_use="observed_bid_ask",
    )
    meta_path = tmp_path / "meta.json"
    meta_path.write_bytes(canonical(metadata))
    return path, meta_path


def test_csv_roundtrip_preserves_quotes_forward_maturities_and_evidence(tmp_path):
    csv_path, meta = write_csv(tmp_path)
    result = import_fx(csv_path, meta, tmp_path / "bundle")
    assert result["evidence_kind"] == "synthetic"
    assert (
        preflight(tmp_path / "bundle", {"line": "fx", "instrument": "forward"})[
            "software_preflight"
        ]
        == "pass"
    )
    manifest, raw = load_bundle(tmp_path / "bundle")
    assert manifest["quote_use"] == "observed_bid_ask"
    assert raw["fx"]["quotes"][0]["available_at"] == demo_data()["fx"]["quotes"][0]["available_at"]


def test_import_rejects_missing_publication_time_before_creating_output(tmp_path):
    csv_path, meta = write_csv(tmp_path)
    csv_path.write_text("observed_at,bid,ask\n2026-01-05,1,1.1\n")
    with pytest.raises(ValueError, match="headers"):
        import_fx(csv_path, meta, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_cli_run_and_verify_with_explicit_configuration(tmp_path, capsys):
    bundle, result = tmp_path / "bundle", tmp_path / "result"
    config = tmp_path / "config.json"
    config.write_text('{"line":"rates"}')
    assert main(["demo", "--output", str(bundle)]) == 0
    assert main(["preflight", "--bundle", str(bundle), "--config", str(config)]) == 0
    assert (
        main(["run", "--bundle", str(bundle), "--config", str(config), "--output", str(result)])
        == 0
    )
    assert main(["verify", "--output", str(result)]) == 0
    assert '"verified": true' in capsys.readouterr().out


def test_cli_missing_arguments_has_nonzero_exit():
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert exc.value.code == 2
