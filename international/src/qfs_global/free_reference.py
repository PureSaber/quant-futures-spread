"""Public vendor-symbol histories. These are NOT dated futures contract chains."""

import json
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from quant_data_kit.derivatives.bundle import digest, json_bytes
from quant_data_kit.derivatives.free_common import MAX_BYTES, public_request
from quant_data_kit.derivatives.models import decimal
from quant_execution.derivative_replay import write_artifacts

SYMBOLS = {
    "CL=F": "WTI crude oil",
    "NG=F": "Natural gas",
    "GC=F": "Gold",
    "SI=F": "Silver",
    "HG=F": "Copper",
    "ZC=F": "Corn",
    "ZW=F": "Wheat",
    "ZS=F": "Soybeans",
    "ES=F": "S&P 500",
    "NQ=F": "Nasdaq 100",
    "YM=F": "Dow",
    "RTY=F": "Russell 2000",
}


def parse_reference(raw, symbol, start, end):
    if len(raw) > MAX_BYTES or symbol not in SYMBOLS:
        raise ValueError("unsupported reference symbol or oversized response")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    payload = json.loads(raw)
    chart = payload["chart"]
    if chart.get("error") or not isinstance(chart.get("result"), list) or len(chart["result"]) != 1:
        raise ValueError("vendor returned an error or ambiguous chart")
    result = chart["result"][0]
    meta = result["meta"]
    if (
        meta.get("symbol") != symbol
        or meta.get("instrumentType") != "FUTURE"
        or not meta.get("currency")
    ):
        raise ValueError("vendor symbol/type/currency mismatch")
    zone = ZoneInfo(meta["exchangeTimezoneName"])
    timestamps = result.get("timestamp", [])
    quotes = result["indicators"]["quote"]
    if len(quotes) != 1 or not timestamps:
        raise ValueError("no unique daily quote series")
    data = quotes[0]
    columns = ("open", "high", "low", "close", "volume")
    if any(len(data.get(k, [])) != len(timestamps) for k in columns):
        raise ValueError("vendor arrays have inconsistent lengths")
    rows, missing, seen = [], [], set()
    for i, stamp in enumerate(timestamps):
        if isinstance(stamp, bool) or not isinstance(stamp, int):
            raise ValueError("integer Unix timestamps required")  # noqa: TRY004 - serialized input
        at = datetime.fromtimestamp(stamp, UTC)
        session = at.astimezone(zone).date()
        if not first <= session <= last:
            continue
        if session in seen:
            raise ValueError("duplicate reference session")
        seen.add(session)
        if any(data[k][i] is None for k in columns):
            missing.append(session.isoformat())
            continue
        numbers = {k: decimal(data[k][i]) for k in columns}
        if (
            not numbers["low"]
            <= min(numbers["open"], numbers["close"])
            <= max(numbers["open"], numbers["close"])
            <= numbers["high"]
            or numbers["volume"] < 0
        ):
            raise ValueError("invalid reference OHLC/volume")
        # Negative futures prices are preserved as observed; these tables never
        # enter the positive-price replay account.
        rows.append(
            {
                "symbol": symbol,
                "session": session.isoformat(),
                "vendor_timestamp": at.isoformat(),
                "currency": meta["currency"],
                **{k: str(v) for k, v in numbers.items()},
            }
        )
    if not rows:
        raise ValueError("empty reference window")
    rows.sort(key=lambda r: r["session"])
    return rows, {
        "symbol": symbol,
        "rows": len(rows),
        "start": rows[0]["session"],
        "end": rows[-1]["session"],
        "null_sessions_excluded": missing,
        "source_sha256": digest(raw),
        "price_basis": "vendor unadjusted OHLC fields; individual contract and vendor roll/adjustment policy unverified",
        "currency": meta["currency"],
    }


def collect_references(output, symbols, start, end, *, fetch=public_request):
    symbols = symbols.split(",") if isinstance(symbols, str) else list(symbols)
    if (
        not symbols
        or len(symbols) > 12
        or len(set(symbols)) != len(symbols)
        or set(symbols) - set(SYMBOLS)
    ):
        raise ValueError("select 1-12 distinct supported vendor symbols")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last - first).days <= 366 * 20 or last >= datetime.now(UTC).date():
        raise ValueError("completed historical dates within a 20-year window required")
    query = urlencode(
        {
            "period1": int(datetime.combine(first, time(), UTC).timestamp()),
            "period2": int(datetime.combine(last + timedelta(days=2), time(), UTC).timestamp()),
            "interval": "1d",
        }
    )
    rows, quality = [], []
    for symbol in symbols:
        raw = fetch(
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            + quote(symbol, safe="")
            + "?"
            + query
        )
        bars, receipt = parse_reference(raw, symbol, start, end)
        rows.extend(bars)
        quality.append(receipt)
    study = {
        "schema": "qfs.vendor-reference/v1",
        "title": "公开期货参考价格历史（不支持合约回放）",
        "evidence_kind": "retrospective_reference",
        "allowed_modes": ["reference_analysis"],
        "input_sha256": digest(json_bytes({"sources": quality, "start": start, "end": end})),
        "provider": "Yahoo Finance public chart",
        "requested_start": start,
        "requested_end": end,
        "coverage": quality,
        "limits": "Public vendor-symbol history, NOT specific delivery contracts or exchange settlement. No verified roll schedule, term structure, historical multiplier, margin or PIT publication time. Cannot validate roll P&L or execute in QExec. Unadjusted OHLC field does not establish vendor roll policy. Missing/null bars disclosed; no exchange-calendar completeness claim. Internal research only, no redistribution rights implied; source access may change.",
    }
    write_artifacts(output, study, {"reference_prices.csv": rows})
    return study
