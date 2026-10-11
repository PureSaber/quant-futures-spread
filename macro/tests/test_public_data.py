from datetime import date

import pytest

from quant_macro.public_data import analyze, import_public, parse_ecb, parse_gsw, parse_holdings
from quant_macro.study import preflight, run, verify

KNOWN = "2026-10-11T00:00:00Z"
START, END = date(2025, 1, 1), date(2026, 10, 11)
XML = (
    b'<Envelope><Cube><Cube time="2026-01-05"><Cube currency="USD" rate="1.1"/>'
    b'<Cube currency="GBP" rate="0.8"/></Cube><Cube time="2026-01-06">'
    b'<Cube currency="USD" rate="1.11"/></Cube></Cube></Envelope>'
)


def test_ecb_import_preserves_vintage_and_never_replays_as_quotes(tmp_path):
    src = tmp_path / "history.xml"
    src.write_bytes(XML)
    receipt = import_public("ecb", src, tmp_path / "bundle", retrieved_at=KNOWN)
    assert receipt["rows"] == 3 and not receipt["original_pit_available"]
    check = preflight(tmp_path / "bundle", dict(line="reference_history"))
    assert check["evidence_kind"] == "retrospective" and check["contracts"] == 2
    run(tmp_path / "bundle", dict(line="reference_history"), tmp_path / "result")
    assert verify(tmp_path / "result")["reproduced"]
    with pytest.raises(ValueError, match="reference-only"):
        preflight(tmp_path / "bundle", dict(line="fx"))


def test_gsw_only_zeros_percent_conversion_and_missing_observations():
    header = "Date," + ",".join(f"SVENY{i:02}" for i in range(1, 31))
    row = "2026-01-05," + ",".join(["4"] + ["NA"] * 29)
    records = parse_gsw(("Preamble\n" + header + "\n" + row).encode(), KNOWN, START, END)
    assert len(records) == 1 and records[0]["value"] == 0.04
    t = analyze(dict(provider="fed-gsw", raw_sha256="a" * 64, records=records), {})
    assert t["reference"][0]["zero_bond_pv"] == pytest.approx(96.0789439152)
    assert t["reference"][0]["dv01"] == pytest.approx(0.00960789439152)
    with pytest.raises(ValueError, match="columns"):
        parse_gsw(b"Date,SVENPY01\n2026-01-05,4", KNOWN, START, END)


def test_xml_duplicates_dtd_and_reference_regressions_rejected():
    with pytest.raises(ValueError, match="DTD"):
        parse_ecb(b"<!DOCTYPE x><Envelope/>", KNOWN, START, END)
    with pytest.raises(ValueError, match="duplicate"):
        parse_ecb(XML.replace(b"2026-01-06", b"2026-01-05"), KNOWN, START, END)
    records = parse_ecb(XML, KNOWN, START, END)
    with pytest.raises(ValueError, match="duplicate"):
        analyze(dict(provider="ecb", raw_sha256="a" * 64, records=[*records, records[0]]), {})


def test_holdings_import_is_value_only_and_checks_snapshot_date():
    content = (
        b'Fund Holdings as of,"Oct 09, 2026"\n'
        b"Name,Asset Class,Market Value,Weight (%),ISIN\n"
        b'Fictional,Fixed Income,"1,000",100,TEST0001\n'
    )
    rows = parse_holdings(content, KNOWN, START, END)
    assert rows[0]["kind"] == "holding_value" and rows[0]["value"] == 1000
    assert "bid" not in rows[0] and "terms" not in rows[0]
    with pytest.raises(ValueError, match="date required"):
        parse_holdings(content.split(b"\n", 1)[1], KNOWN, START, END)
