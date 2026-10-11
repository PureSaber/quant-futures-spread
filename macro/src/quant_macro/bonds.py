"""Point-in-time rates, credit and bond ETF valuation studies."""

from .common import currency, day, integer, keys, nonempty, number, positive, stamp
from .fixed_income import Bond, Curve, risk, solve_spread, stir_future, treasury_future_risk

DEFAULTS = {
    "line": "rates",
    "as_of": "2026-01-05T17:00:00+00:00",
    "settlement": "2026-01-05",
    "max_quote_age_days": 3,
    "max_curve_age_days": 3,
    "max_holdings_age_days": 31,
    "stale_policy": "reject",
    "model_spread": 0.01,
    "shock_bp": 100,
}


def configuration(raw):
    keys(raw, (), DEFAULTS)
    c = DEFAULTS | raw
    if c["line"] not in {"rates", "credit"}:
        raise ValueError("rates or credit line required")
    if c["stale_policy"] not in {"reject", "model"}:
        raise ValueError("stale_policy must be reject or model")
    # Curve tenors are measured from settlement, which must be explicit.
    if day(c["settlement"]) < stamp(c["as_of"]).date():
        raise ValueError("settlement cannot precede as_of date")
    for key in ("max_quote_age_days", "max_curve_age_days", "max_holdings_age_days"):
        c[key] = integer(c[key], key, 0, 3650)
    c["model_spread"] = number(c["model_spread"])
    c["shock_bp"] = number(c["shock_bp"])
    return c


def _availability(row, as_of, max_days):
    observed, available = stamp(row["observed_at"]), stamp(row["available_at"])
    if observed > available:
        raise ValueError("observation cannot follow publication")
    if available > as_of:
        return "future"
    return "stale" if (as_of - observed).total_seconds() > max_days * 86400 else "fresh"


def _quotes(rows, as_of, max_days):
    result, seen = {}, set()
    if not isinstance(rows, list):
        raise ValueError("quotes must be a list")
    for row in rows:
        keys(row, ("instrument_id", "observed_at", "available_at", "bid", "ask", "kind"))
        nonempty(row["instrument_id"], "instrument_id")
        if row["kind"] not in {"trade", "dealer", "valuation"}:
            raise ValueError("quote kind must be trade, dealer or valuation")
        if positive(row["bid"]) > positive(row["ask"]):
            raise ValueError("crossed bond/ETF quote")
        key = row["instrument_id"], stamp(row["observed_at"]), stamp(row["available_at"])
        if key in seen:
            raise ValueError("duplicate quote observation")
        seen.add(key)
        state = _availability(row, as_of, max_days)
        if state == "future":
            continue
        prior = result.get(row["instrument_id"])
        if prior is None or key[1:] > prior[0]:
            result[row["instrument_id"]] = key[1:], row, state
    return {k: (r, state) for k, (_, r, state) in result.items()}


def analyze(raw, raw_config):
    c = configuration(raw_config)
    keys(raw, ("bonds", "curves", "bond_quotes"), ("etfs", "etf_quotes", "rate_futures"))
    now, settlement = stamp(c["as_of"]), day(c["settlement"])
    curves = []
    for r in raw["curves"]:
        keys(r, ("observed_at", "available_at", "currency", "anchor", "nodes"))
        currency(r["currency"])
        Curve.parse(r["nodes"])
        state = _availability(r, now, c["max_curve_age_days"])
        if state == "fresh" and day(r["anchor"]) == settlement:
            curves.append(r)
    if not curves:
        raise ValueError("fresh published zero curve with settlement anchor required")
    currencies = {r["currency"] for r in curves}
    if len(currencies) != 1:
        raise ValueError("one currency per bond study; split currencies explicitly")
    curve_keys = [(stamp(r["observed_at"]), stamp(r["available_at"])) for r in curves]
    if len(set(curve_keys)) != len(curve_keys):
        raise ValueError("duplicate curve publication")
    curve_raw = max(curves, key=lambda r: (stamp(r["observed_at"]), stamp(r["available_at"])))
    curve = Curve.parse(curve_raw["nodes"])
    ccy = curve_raw["currency"]
    all_bonds, selected = {}, {}
    for record in raw["bonds"]:
        keys(record, ("terms", "sector", "available_at"))
        bond = Bond.parse(record["terms"])
        if bond.instrument_id in all_bonds:
            raise ValueError("duplicate bond identifier")
        all_bonds[bond.instrument_id] = bond
        if record["sector"] not in {"government", "corporate"}:
            raise ValueError("government/corporate sector required")
        if stamp(record["available_at"]) <= now and (
            c["line"] == "credit" or record["sector"] == "government"
        ):
            if bond.currency != ccy:
                raise ValueError("bond/curve currency mismatch")
            selected[bond.instrument_id] = bond
    if not selected:
        raise ValueError("no published bonds in selected research line")
    if any(r["instrument_id"] not in all_bonds for r in raw["bond_quotes"]):
        raise ValueError("quote references unknown bond")
    quotes = _quotes(raw["bond_quotes"], now, c["max_quote_age_days"])
    valuations, flows, scenarios = [], [], []
    for instrument_id, bond in selected.items():
        q, state = quotes.get(instrument_id, (None, "missing"))
        if state != "fresh" and c["stale_policy"] == "reject":
            raise ValueError(f"{instrument_id}: {state} bond quote")
        clean = (number(q["bid"]) + number(q["ask"])) / 2 if q else None
        spread = (
            solve_spread(bond, settlement, curve, clean) if state == "fresh" else c["model_spread"]
        )
        measure = risk(bond, settlement, curve, spread)
        valuation = {
            "instrument_id": instrument_id,
            "currency": ccy,
            "quote_state": state,
            "quote_kind": q["kind"] if q else "missing",
            "observed_clean": clean,
            "quote_observed_at": q["observed_at"] if q else "",
            "valuation_basis": "observed_mid" if state == "fresh" else "model",
            # Valuation feeds and modeled prices are never executable quotes.
            "has_fresh_market_quote": state == "fresh" and q["kind"] != "valuation",
            **measure,
        }
        valuations.append(valuation)
        for when, amount in bond.flows(settlement):
            flows.append(
                {
                    "instrument_id": instrument_id,
                    "payment": when.isoformat(),
                    "cashflow_per_100": amount,
                    "currency": ccy,
                }
            )
        shocked = risk(bond, settlement, curve, spread + c["shock_bp"] / 10000)
        scenarios.append(
            {
                "instrument_id": instrument_id,
                "parallel_shock_bp": c["shock_bp"],
                "base_dirty": measure["dirty"],
                "shocked_dirty": shocked["dirty"],
                "pnl_per_100": shocked["dirty"] - measure["dirty"],
            }
        )

    lookup = {r["instrument_id"]: r for r in valuations}
    futures = []
    future_ids = set()
    for f in raw.get("rate_futures", []):
        common = ("instrument_id", "kind", "currency", "observed_at", "available_at", "rule_source")
        nonempty(f.get("instrument_id"), "future instrument_id")
        if f["instrument_id"] in future_ids:
            raise ValueError("duplicate rate future identifier")
        future_ids.add(f["instrument_id"])
        nonempty(f.get("rule_source"), "rule_source")
        if currency(f["currency"]) != ccy:
            raise ValueError("rate future currency mismatch")
        state = _availability(f, now, c["max_quote_age_days"])
        if state == "stale":
            raise ValueError("stale rate future quote")
        if f["kind"] == "treasury":
            keys(f, common + ("ctd_id", "conversion_factor", "face_per_contract", "price"))
            if state == "future":
                continue
            if f["ctd_id"] not in lookup:
                raise ValueError("CTD must have a valuation in the selected study")
            ctd = lookup[f["ctd_id"]]
            cf = positive(f["conversion_factor"])
            futures.append(
                {
                    "instrument_id": f["instrument_id"],
                    "kind": "treasury",
                    "dv01_per_contract": treasury_future_risk(
                        ctd["dv01_per_100"], cf, f["face_per_contract"]
                    ),
                    "gross_basis_per_100": ctd["clean"] - number(f["price"]) * cf,
                    "ctd_id": f["ctd_id"],
                    "ctd_valuation_basis": ctd["valuation_basis"],
                }
            )
        elif f["kind"] == "stir":
            keys(f, common + ("price", "value_per_price_point"))
            if state == "future":
                continue
            futures.append(
                {
                    "instrument_id": f["instrument_id"],
                    "kind": "stir",
                    **stir_future(f["price"], c["shock_bp"], f["value_per_price_point"]),
                }
            )
        else:
            raise ValueError("unsupported rate future quote convention")

    etfs, components = [], []
    if c["line"] == "credit":
        etf_quotes = _quotes(raw.get("etf_quotes", []), now, c["max_quote_age_days"])
        snapshots = {}
        seen = set()
        for e in raw.get("etfs", []):
            keys(
                e,
                (
                    "instrument_id",
                    "currency",
                    "observed_at",
                    "available_at",
                    "shares",
                    "cash",
                    "liabilities",
                    "holdings",
                ),
            )
            nonempty(e["instrument_id"], "ETF instrument_id")
            key = e["instrument_id"], stamp(e["observed_at"]), stamp(e["available_at"])
            if key in seen:
                raise ValueError("duplicate ETF holdings publication")
            seen.add(key)
            state = _availability(e, now, c["max_holdings_age_days"])
            if state == "future":
                continue
            prior = snapshots.get(e["instrument_id"])
            if prior is None or key[1:] > prior[0]:
                snapshots[e["instrument_id"]] = key[1:], e, state
        for _, e, state in snapshots.values():
            if state != "fresh":
                raise ValueError("stale ETF holdings; current basket cannot be inferred")
            if currency(e["currency"]) != ccy:
                raise ValueError("ETF/constituent currency mismatch")
            shares = positive(e["shares"], "shares outstanding")
            total = positive(e["cash"], "cash", zero=True)
            total -= positive(e["liabilities"], "liabilities", zero=True)
            dv01, modeled, held = 0.0, False, set()
            if not e["holdings"]:
                raise ValueError("ETF requires explicit constituent holdings")
            for h in e["holdings"]:
                keys(h, ("instrument_id", "face"))
                bid = h["instrument_id"]
                if bid in held or bid not in lookup:
                    raise ValueError("duplicate/missing ETF constituent valuation")
                held.add(bid)
                face = positive(h["face"], "holding face")
                v = lookup[bid]
                value = face / 100 * v["dirty"]
                total += value
                dv01 += face / 100 * v["dv01_per_100"]
                modeled |= v["valuation_basis"] == "model"
                components.append(
                    {
                        "etf_id": e["instrument_id"],
                        "instrument_id": bid,
                        "face": face,
                        "dirty_value": value,
                        "valuation_basis": v["valuation_basis"],
                    }
                )
            nav = positive(total, "basket net assets") / shares
            q, qstate = etf_quotes.get(e["instrument_id"], (None, "missing"))
            if qstate != "fresh" and c["stale_policy"] == "reject":
                raise ValueError("stale/missing ETF quote")
            market = (number(q["bid"]) + number(q["ask"])) / 2 if qstate == "fresh" else None
            etfs.append(
                {
                    "instrument_id": e["instrument_id"],
                    "currency": ccy,
                    "basket_nav": nav,
                    "market_mid": market,
                    "premium_discount": market / nav - 1 if market is not None else None,
                    "basket_basis": "contains_model_values" if modeled else "observed_mid",
                    "dv01_per_share": dv01 / shares,
                    "effective_duration": dv01 / (total * 0.0001),
                    "holdings_observed_at": e["observed_at"],
                    "quote_state": qstate,
                }
            )
    return {
        "valuations": valuations,
        "cashflows": flows,
        "scenarios": scenarios,
        "rate_futures": futures,
        "etfs": etfs,
        "components": components,
    }
