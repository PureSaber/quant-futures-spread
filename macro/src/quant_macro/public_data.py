"""Public reference history. Never manufacture executable prices or PIT vintages."""

import csv
import hashlib
import math
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from .common import canonical, currency, day, keys, nonempty, number, positive, stamp

SOURCES = {
    "ecb": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml",
    "fed-gsw": "https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv",
    "lqd-holdings": "https://www.ishares.com/us/products/239566/ishares-iboxx-investment-grade-corporate-bond-etf/latest-holdings.csv",
}
LIMITS = {
    "ecb": "ECB reference rates are informational, not tradable bid/ask. Latest downloaded "
    "vintage; original publication timestamps not reconstructed.",
    "fed-gsw": "Federal Reserve staff fitted Treasury zeros, continuously compounded annual "
    "percent; subject to revisions, not official statistical release or observed bond prices. "
    "Latest vintage, no historical PIT claim.",
    "lqd-holdings": "Issuer investment-book holdings use vendor valuations; not executable "
    "quotes, official fund NAV or AP creation basket. Coupon dates and call terms must be "
    "obtained separately.",
}


def numeric(text):
    return number(text.replace(",", "").strip())


def parse_ecb(content, retrieved, start, end):
    # ElementTree does not resolve external entities; reject DTDs explicitly as well.
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("XML DTD/entity declarations are not accepted")
    root, result, seen = ET.fromstring(content), [], set()
    for node in root.iter():
        if "time" not in node.attrib:
            continue
        date = day(node.attrib["time"])
        if date in seen:
            raise ValueError("duplicate ECB observation date")
        seen.add(date)
        if not start <= date <= end:
            continue
        currencies = set()
        for child in node:
            ccy = currency(child.attrib["currency"])
            if ccy in currencies or ccy == "EUR":
                raise ValueError("duplicate or invalid ECB currency")
            currencies.add(ccy)
            result.append(
                dict(
                    date=str(date),
                    instrument_id="ECB:EUR/" + ccy,
                    kind="fx_reference",
                    value=positive(child.attrib["rate"]),
                    unit=ccy + " per EUR",
                    tenor_years=0,
                    known_at=retrieved,
                )
            )
    return result


def parse_gsw(content, retrieved, start, end):
    lines = content.decode("utf-8-sig").splitlines()
    offset = next((i for i, line in enumerate(lines) if line.startswith("Date,")), None)
    if offset is None:
        raise ValueError("GSW CSV header missing")
    reader = csv.DictReader(lines[offset:])
    required = {"Date"} | {f"SVENY{i:02}" for i in range(1, 31)}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("GSW continuous zero-yield columns missing")
    result, seen = [], set()
    for row in reader:
        date = day(row["Date"])
        if date in seen:
            raise ValueError("duplicate GSW date")
        seen.add(date)
        if not start <= date <= end:
            continue
        for tenor in range(1, 31):
            value = row[f"SVENY{tenor:02}"]
            if value in {"NA", "", "N/A"}:
                continue
            result.append(
                dict(
                    date=str(date),
                    instrument_id=f"GSW:USD:{tenor}Y",
                    kind="continuous_zero",
                    value=number(value) / 100,
                    unit="annual decimal continuous",
                    tenor_years=tenor,
                    known_at=retrieved,
                )
            )
    return result


def parse_holdings(content, retrieved, start, end):
    lines = content.decode("utf-8-sig").splitlines()
    header = next(
        (
            i
            for i, line in enumerate(lines)
            if line.startswith("Name,")
            or line.startswith('"Name",')
            or line.startswith("Ticker,")
            or line.startswith('"Ticker",')
        ),
        None,
    )
    if header is None:
        raise ValueError("issuer holdings CSV header missing")
    dated = [row for row in csv.reader(lines[:header]) if row and "Fund Holdings as of" in row[0]]
    if len(dated) != 1:
        raise ValueError("issuer snapshot date required")
    date = datetime.strptime(dated[0][1], "%b %d, %Y").date()
    if not start <= date <= end:
        raise ValueError("holdings snapshot outside requested range")
    reader = csv.DictReader(lines[header:])
    required = {"Name", "Market Value", "Weight (%)", "Asset Class"}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("issuer holdings columns missing")
    result, seen = [], set()
    for row in reader:
        if not row.get("Asset Class") or not row.get("Market Value"):
            continue  # issuer disclaimer/footer, not a security row
        ident = row.get("ISIN") or row.get("CUSIP") or row.get("Ticker") or row["Name"]
        if ident in {"-", ""}:
            ident = row["Name"]
        if ident in seen:
            raise ValueError("duplicate issuer holding identity")
        seen.add(ident)
        result.append(
            dict(
                date=str(date),
                instrument_id="LQD:" + ident,
                kind="holding_value",
                value=numeric(row["Market Value"]),
                unit="USD vendor market value",
                tenor_years=0,
                known_at=retrieved,
                name=row["Name"],
                asset_class=row["Asset Class"],
                weight_percent=numeric(row["Weight (%)"]),
            )
        )
    return result


def import_public(
    provider, source_file, output, *, start="2025-01-01", end=None, retrieved_at=None
):
    if provider not in SOURCES:
        raise ValueError("unknown public provider")
    retrieved_at = retrieved_at or datetime.now(UTC).isoformat()
    retrieved = stamp(retrieved_at)
    start, end = day(start), day(end) if end else retrieved.date()
    if start > end or end > retrieved.date():
        raise ValueError("invalid public-data date range")
    content = Path(source_file).read_bytes()
    if len(content) > 32 * 1024 * 1024:
        raise ValueError("source exceeds 32 MiB limit")
    parser = {"ecb": parse_ecb, "fed-gsw": parse_gsw, "lqd-holdings": parse_holdings}[provider]
    records = parser(content, retrieved_at, start, end)
    if not records:
        raise ValueError("no usable public records in requested range")
    records.sort(key=lambda r: (r["date"], r["instrument_id"]))
    raw_hash = hashlib.sha256(content).hexdigest()
    data = canonical(
        {"reference": {"provider": provider, "raw_sha256": raw_hash, "records": records}}
    )
    manifest = dict(
        schema_version="quant.macro.bundle/v1",
        evidence_kind="retrospective",
        source=SOURCES[provider],
        license="Source website terms apply; no redistribution licence inferred",
        retrieved_at=retrieved_at,
        data_sha256=hashlib.sha256(data).hexdigest(),
        quote_use="reference_only",
        limits=LIMITS[provider],
    )
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    (root / "source-raw.bin").write_bytes(content)
    (root / "data.json").write_bytes(data)
    (root / "manifest.json").write_bytes(canonical(manifest))
    receipt = dict(
        provider=provider,
        rows=len(records),
        instruments=len({r["instrument_id"] for r in records}),
        first_date=records[0]["date"],
        last_date=records[-1]["date"],
        raw_sha256=raw_hash,
        bundle=str(root.resolve()),
        execution_ready=False,
        original_pit_available=False,
    )
    (root / "import-receipt.json").write_bytes(canonical(receipt))
    return receipt


def download_public(provider, output, **kwargs):
    if provider not in SOURCES:
        raise ValueError("unknown public provider")
    # Fixed public endpoints only; no credential handling and no anti-bot bypass.
    request = urllib.request.Request(
        SOURCES[provider], headers={"User-Agent": "quant-macro-research/0.2"}
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        content = response.read(32 * 1024 * 1024 + 1)
    if len(content) > 32 * 1024 * 1024:
        raise ValueError("download exceeds 32 MiB limit")
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "source.bin"
        path.write_bytes(content)
        return import_public(provider, path, output, **kwargs)


DEFAULTS = {
    "line": "reference_history",
    "series": "",
    "start": "2025-01-01",
    "end": "",
    "shock_bp": 100,
}


def configuration(raw):
    keys(raw, (), DEFAULTS)
    c = DEFAULTS | raw
    if c["line"] != "reference_history" or not isinstance(c["series"], str):
        raise ValueError("invalid reference configuration")
    day(c["start"])
    if c["end"] and day(c["end"]) < day(c["start"]):
        raise ValueError("invalid date window")
    c["shock_bp"] = number(c["shock_bp"])
    return c


def analyze(raw, config):
    c = configuration(config)
    keys(raw, ("provider", "raw_sha256", "records"))
    nonempty(raw["raw_sha256"], "raw source hash")
    if raw["provider"] not in SOURCES:
        raise ValueError("unsupported source")
    prior, records, stats = {}, [], {}
    for r in raw["records"]:
        keys(
            r,
            ("date", "instrument_id", "kind", "value", "unit", "tenor_years", "known_at"),
            ("name", "asset_class", "weight_percent"),
        )
        date = day(r["date"])
        if stamp(r["known_at"]).date() < date:
            raise ValueError("reference known before observation date")
        if r["kind"] not in {"fx_reference", "continuous_zero", "holding_value"}:
            raise ValueError("unknown reference kind")
        ident, value = r["instrument_id"], number(r["value"])
        if c["series"] and ident != c["series"]:
            continue
        if date < day(c["start"]) or (c["end"] and date > day(c["end"])):
            continue
        if ident in prior and date <= day(prior[ident]["date"]):
            raise ValueError("duplicate/regressing reference observations")
        record = dict(r, change=value - number(prior[ident]["value"]) if ident in prior else "")
        if r["kind"] == "continuous_zero":
            t = positive(r["tenor_years"])
            pv = 100 * math.exp(-value * t)
            record.update(
                zero_bond_pv=pv,
                dv01=pv * t / 10000,
                shocked_pv=100 * math.exp(-(value + c["shock_bp"] / 10000) * t),
            )
        elif r["kind"] == "fx_reference":
            positive(value)
            record["reference_return"] = (
                value / number(prior[ident]["value"]) - 1 if ident in prior else ""
            )
        prior[ident] = r
        records.append(record)
        if ident not in stats:
            stats[ident] = dict(instrument_id=ident, first_date=r["date"], observations=0)
        stats[ident].update(last_date=r["date"], observations=stats[ident]["observations"] + 1)
    if not records:
        raise ValueError("empty reference selection")
    return dict(reference=records, coverage=list(stats.values()))
