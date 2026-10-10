"""Unadjusted contract curves and causal roll research; no broker connectivity."""

import itertools
from datetime import date, timedelta

from quant_data_kit.derivatives import load_bundle
from quant_data_kit.derivatives.models import decimal
from quant_execution.derivative_replay import replay, write_artifacts

DEFAULTS = {
    "mode": "replay",
    "product": "DEMO",
    "strategy": "nearby",
    "roll_rule": "expiry",
    "roll_days": 3,
    "contracts": 1,
    "initial_cash": 100000,
    "fee_per_contract": 1,
    "slippage_ticks": 1,
    "start": "",
    "end": "",
}


def configuration(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError("unknown futures configuration fields")
    c = DEFAULTS | raw
    if (
        c["mode"] not in {"analysis", "replay"}
        or c["strategy"] not in {"nearby", "calendar_spread"}
        or c["roll_rule"] not in {"expiry", "volume"}
    ):
        raise ValueError("unsupported research mode/strategy/roll rule")
    if not isinstance(c["product"], str) or not 1 <= len(c["product"]) <= 100:
        raise ValueError("explicit product required")
    for key, lo, hi in [
        ("roll_days", 1, 60),
        ("contracts", 1, 10000),
        ("initial_cash", 1, 1e12),
        ("fee_per_contract", 0, 1e6),
        ("slippage_ticks", 0, 1000),
    ]:
        number = decimal(c[key])
        if not lo <= number <= hi:
            raise ValueError(f"{key} outside supported range")
        if key in {"roll_days", "contracts", "slippage_ticks"} and number != int(number):
            raise ValueError(f"{key} must be integer")
    for key in ("start", "end"):
        if c[key]:
            date.fromisoformat(c[key])
    if c["start"] and c["end"] and c["start"] > c["end"]:
        raise ValueError("start follows end")
    return c


def selector(bundle, config):
    def decide(quotes, now):
        available = [
            bundle.contract(i)
            for i in quotes
            if bundle.contract(i).product == config["product"]
            and bundle.contract(i).last_trade_at > now + timedelta(days=int(config["roll_days"]))
            and quotes[i].volume > 0
        ]
        available.sort(key=lambda c: (c.expiry, c.instrument_id))
        if config["roll_rule"] == "volume" and available:
            front = max(
                available, key=lambda c: (quotes[c.instrument_id].volume, -c.expiry.timestamp())
            )
            available = [front] + [c for c in available if c.expiry > front.expiry]
        if not available or (config["strategy"] == "calendar_spread" and len(available) < 2):
            return {}  # explicitly flat when a complete eligible curve is unavailable
        quantity = decimal(config["contracts"])
        target = {available[0].instrument_id: quantity}
        if config["strategy"] == "calendar_spread":
            target[available[1].instrument_id] = -quantity
        return target

    return decide


def preflight(bundle, raw):
    config = configuration(raw)
    contracts = bundle.contracts
    if any(c.kind != "future" for c in contracts):
        raise ValueError("futures study requires a future-only bundle")
    if len({c.currency for c in contracts}) != 1 or len({c.venue for c in contracts}) != 1:
        raise ValueError(
            "one currency and venue per futures study; split exchanges before comparison"
        )
    selected = [c for c in contracts if c.product == config["product"]]
    if not selected:
        raise ValueError("requested product is absent")
    if len({c.multiplier for c in selected}) != 1:
        raise ValueError("calendar research requires consistent product multipliers")
    if config["strategy"] == "calendar_spread" and len(selected) < 2:
        raise ValueError("calendar spread needs at least two maturities")
    if config["mode"] == "replay":
        if any(c.initial_margin is None for c in selected):
            raise ValueError("explicit initial and maintenance margins required")
        if any(
            q.settlement is None or q.settlement <= 0
            for q in bundle.quotes
            if bundle.contract(q.instrument_id).product == config["product"]
        ):
            raise ValueError(
                "futures replay needs explicit positive settlement prices; OHLC alone is analysis-only"
            )
        sessions = {
            q.session
            for q in bundle.quotes
            if (not config["start"] or q.session >= config["start"])
            and (not config["end"] or q.session <= config["end"])
        }
        if len(sessions) < 3:
            raise ValueError("replay needs at least three sessions")
    return {
        **bundle.summary(),
        "software_preflight": "pass",
        "investable": False,
        "mode": config["mode"],
        "product": config["product"],
        "roll_basis": "previous observed volume/expiry; next observed daily open execution",
        "settlement": "explicit settlement; no settlement inferred from close",
        "margin": "sum of declared per-contract margin, no calendar offsets",
    }


def run(bundle_path, raw, output):
    bundle = load_bundle(bundle_path)
    config = configuration(raw)
    check = preflight(bundle, config)
    curve = []
    by_session = {}
    for q in bundle.quotes:
        c = bundle.contract(q.instrument_id)
        if c.product != config["product"] or c.known_at > q.available_at:
            continue
        curve.append(
            {
                "session": q.session,
                "instrument_id": c.instrument_id,
                "expiry": c.expiry.isoformat(),
                "days_to_expiry": (c.expiry - q.at).total_seconds() / 86400,
                "close": str(q.close),
                "settlement": str(q.settlement) if q.settlement is not None else "",
                "volume": str(q.volume),
                "currency": c.currency,
            }
        )
        by_session.setdefault(q.session, []).append((c, q))
    spreads = []
    for session, pairs in by_session.items():
        pairs.sort(key=lambda pair: pair[0].expiry)
        for (near, q1), (far, q2) in itertools.pairwise(pairs):
            if q1.at != q2.at:
                raise ValueError("spread observations must be synchronized")
            spreads.append(
                {
                    "session": session,
                    "near": near.instrument_id,
                    "far": far.instrument_id,
                    "near_minus_far": str(q1.close - q2.close),
                    "spread_value_per_pair": str((q1.close - q2.close) * near.multiplier),
                }
            )
    tables = {"curve.csv": curve, "spreads.csv": spreads}
    replayed = {}
    if config["mode"] == "replay":
        replayed = replay(bundle, config, selector(bundle, config))
        for name in ("nav", "fills", "positions", "lifecycle", "ledger"):
            tables[name + ".csv"] = replayed[name]
        previous = None
        rolls = []
        for row in replayed["decisions"]:
            if row["targets"] != previous:
                rolls.append(
                    {
                        "decision_at": row["decision_at"],
                        "previous_targets": str(previous or {}),
                        "next_targets": str(row["targets"]),
                        "execution": "next observed bar open",
                    }
                )
            previous = row["targets"]
        tables["rolls.csv"] = rolls
    study = {
        "title": "海外期货期限结构、价差与换月研究",
        "evidence_kind": bundle.manifest["evidence_kind"],
        "currency": bundle.contracts[0].currency,
        "input_sha256": bundle.identity,
        "config": config,
        "final_account": replayed.get("final_account"),
        "decisions": replayed.get("decisions", []),
        "roll_basis": check["roll_basis"],
        "margin": check["margin"],
        "limits": bundle.manifest["limits"]
        + " Original contract prices only; no back-adjusted series traded. "
        "Insufficient eligible maturities means flat. Physical delivery is avoided by rolling; "
        "missing roll liquidity or settlement fails the replay. Fees, slippage and margin are declared research assumptions.",
    }
    bundle.verify_unchanged()
    write_artifacts(output, study, tables)
    return study
