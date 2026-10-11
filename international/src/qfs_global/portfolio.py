"""Same-currency futures baskets through the existing QExec research account."""

from datetime import timedelta

from quant_data_kit.derivatives import load_bundle
from quant_data_kit.derivatives.bundle import digest, json_bytes
from quant_data_kit.derivatives.models import decimal, utc
from quant_execution.derivative_replay import replay, write_artifacts

from .study import configuration, selector


def portfolio_selector(bundle, raw):
    if not isinstance(raw, dict) or set(raw) != {"account", "legs", "delivery_deadlines"}:
        raise ValueError("portfolio needs account, legs and delivery_deadlines")
    account = configuration(raw["account"])
    if account["mode"] != "replay" or not raw["legs"]:
        raise ValueError("nonempty replay portfolio required")
    bundle.require_mode("replay")
    if (
        any(c.kind != "future" for c in bundle.contracts)
        or len({c.currency for c in bundle.contracts}) != 1
    ):
        raise ValueError("future-only, same-currency portfolio required")
    deadlines = {}
    for instrument_id, info in raw["delivery_deadlines"].items():
        c = bundle.contract(instrument_id)
        if set(info) != {"at", "known_at", "source"} or not info["source"]:
            raise ValueError("delivery deadline needs at, known_at and source")
        at, known = utc(info["at"]), utc(info["known_at"])
        if not c.listed_at < at <= c.last_trade_at or known >= at:
            raise ValueError("invalid delivery deadline or availability")
        deadlines[instrument_id] = (at, known)
    selectors, seen, selected = [], set(), set()
    for leg in raw["legs"]:
        if not isinstance(leg, dict) or set(leg) != {
            "product",
            "direction",
            "contracts",
            "strategy",
            "roll_rule",
            "roll_days",
        }:
            raise ValueError(
                "each leg requires product/direction/contracts/strategy/roll_rule/roll_days"
            )
        if (
            isinstance(leg["direction"], bool)
            or leg["direction"] not in {-1, 1}
            or leg["product"] in seen
        ):
            raise ValueError("unique products and direction +/-1 required")
        seen.add(leg["product"])
        cfg = configuration(account | {k: v for k, v in leg.items() if k != "direction"})
        contracts = [c for c in bundle.contracts if c.product == cfg["product"]]
        if not contracts or len({(c.venue, c.multiplier) for c in contracts}) != 1:
            raise ValueError("each selected product needs one venue and consistent multiplier")
        if cfg["strategy"] == "calendar_spread" and len({c.expiry for c in contracts}) < 2:
            raise ValueError("calendar spread requires distinct maturities")
        for c in contracts:
            selected.add(c.instrument_id)
            if c.initial_margin is None or (
                c.settlement == "physical" and c.instrument_id not in deadlines
            ):
                raise ValueError("explicit margins and physical delivery deadlines required")
        selectors.append((selector(bundle, cfg), leg["direction"], cfg["roll_days"]))
    if any(
        q.settlement is None or min(q.open, q.low, q.close, q.settlement) <= 0
        for q in bundle.quotes
        if q.instrument_id in selected
    ):
        raise ValueError("selected futures need positive raw prices and explicit settlement")

    def decide(quotes, now):
        target = {}
        for select, direction, days in selectors:
            eligible = {
                i: q
                for i, q in quotes.items()
                if i not in deadlines
                or (deadlines[i][1] <= now and now + timedelta(days=days) < deadlines[i][0])
            }
            target.update({i: direction * qty for i, qty in select(eligible, now).items()})
        return target

    # Check both the decision and the following fill date: a long holiday or
    # data gap must not carry an existing position through first notice.
    groups = {}
    for q in bundle.quotes:
        if (not account["start"] or q.session >= account["start"]) and (
            not account["end"] or q.session <= account["end"]
        ):
            session = groups.setdefault(q.session, {})
            if q.instrument_id in session:
                raise ValueError("one bar per instrument/session required for daily replay")
            if session and any(other.at != q.at for other in session.values()):
                raise ValueError("portfolio observations must be synchronized across venues")
            session[q.instrument_id] = q
    previous, held = {}, set()
    for session in sorted(groups):
        observed = groups[session]
        fill_at = min(q.at for q in observed.values())
        for instrument_id in held | set(previous):
            if instrument_id in deadlines and deadlines[instrument_id][0] <= fill_at:
                raise ValueError("next observed session crosses delivery deadline; roll earlier")
        held = set(previous)
        now = max(q.available_at for q in observed.values())
        eligible = {
            i: q
            for i, q in observed.items()
            if bundle.contract(i).known_at <= now < bundle.contract(i).last_trade_at
        }
        previous = {i: qty for i, qty in decide(eligible, now).items() if decimal(qty)}
    return account, decide


def run_portfolio(bundle_path, config, output):
    bundle = load_bundle(bundle_path)
    account, decide = portfolio_selector(bundle, config)
    result = replay(bundle, account, decide)
    study = {
        "title": "跨品种期货组合与换月研究",
        "evidence_kind": bundle.manifest["evidence_kind"],
        "currency": bundle.contracts[0].currency,
        "input_sha256": digest(json_bytes({"bundle": bundle.identity, "config": config})),
        "bundle_sha256": bundle.identity,
        "config": config,
        "final_account": result["final_account"],
        "decisions": result["decisions"],
        "limits": "QExec research replay; same currency, synchronized daily observations; integer fixed contract weights, additive declared margin, no SPAN offsets. Original unadjusted prices; next observed open fills, explicit fees/slippage. Physical delivery deadlines required. No broker access or investability claim. "
        + bundle.manifest["limits"],
    }
    tables = {
        name + ".csv": result[name] for name in ("nav", "fills", "positions", "lifecycle", "ledger")
    }
    tables["rolls.csv"] = [
        {"decision_at": r["decision_at"], "targets": str(r["targets"])}
        for i, r in enumerate(result["decisions"])
        if i == 0 or r["targets"] != result["decisions"][i - 1]["targets"]
    ]
    write_artifacts(output, study, tables)
    return study
