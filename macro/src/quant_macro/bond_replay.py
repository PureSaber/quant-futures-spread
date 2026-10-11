"""Daily bond/ETF secondary-market account, deliberately outside certified paths.

Bond quantity is face, ETF quantity is shares. Settlement-date dirty consideration,
coupon/principal events, ETF ex-date receivables and short manufactured payments
are booked separately. Stale marks never authorize a fill.
"""

from datetime import timedelta
from decimal import Decimal

from .calendars import Calendar, settlement
from .common import currency, day, decimal, integer, keys, number, positive, stamp
from .fixed_income import Bond

DEFAULTS = {
    "line": "bond_replay",
    "initial_cash": 100000,
    "strategy": "allocation",
    "weights": {"DEMO:CORP": 0.5},
    "allocation_instrument": "",
    "allocation_weight": 0.5,
    "reinvest": True,
    "etf_id": "DEMO:BOND-ETF",
    "etf_units": 50,
    "entry_bps": 20,
    "fee_bps": 1,
    "slippage_bps": 0,
    "borrowing_spread": 0.01,
    "max_leverage": 2,
    "max_mark_age_days": 7,
    "max_gap_days": 7,
    "max_quote_age_hours": 24,
    "close_on": "2026-01-12",
}


def configuration(raw):
    keys(raw, (), DEFAULTS)
    c = DEFAULTS | raw
    if not isinstance(c["allocation_instrument"], str):
        raise ValueError("allocation instrument must be text")
    positive(c["allocation_weight"], "allocation weight", zero=True)
    if c["allocation_instrument"]:
        c["weights"] = {c["allocation_instrument"]: c["allocation_weight"]}
    if c["line"] != "bond_replay" or c["strategy"] not in {"allocation", "etf_relative_value"}:
        raise ValueError("invalid bond replay strategy")
    if not isinstance(c["reinvest"], bool) or not isinstance(c["weights"], dict):
        raise ValueError("boolean reinvest and allocation weights required")
    for v in c["weights"].values():
        positive(v, "weight", zero=True)
    if sum(c["weights"].values()) > 1 or not c["weights"]:
        raise ValueError("nonempty long allocation weights must sum to at most one")
    for key in ("initial_cash", "etf_units", "max_leverage", "max_gap_days"):
        c[key] = positive(c[key], key)
    for key in ("entry_bps", "fee_bps", "slippage_bps", "borrowing_spread", "max_quote_age_hours"):
        c[key] = positive(c[key], key, zero=True)
    c["max_mark_age_days"] = integer(c["max_mark_age_days"], "mark age", 0, 365)
    if max(c["fee_bps"], c["slippage_bps"]) >= 10000:
        raise ValueError("cost must be below 10000 bps")
    day(c["close_on"])
    return c


def replay(raw, raw_config):
    c = configuration(raw_config)
    keys(raw, ("currency", "calendar", "instruments", "observations", "distributions", "baskets"))
    currency(raw["currency"])
    calendar = Calendar(raw["calendar"])
    rows = raw["observations"]
    if len(rows) < 3:
        raise ValueError("at least three account observations required")
    first = stamp(rows[0]["available_at"])
    instruments, bonds, scales = {}, {}, {}
    for r in raw["instruments"]:
        keys(r, ("instrument_id", "kind", "available_at", "settlement_lag"), ("terms",))
        ident = r["instrument_id"]
        if not isinstance(ident, str) or not ident or ident in instruments:
            raise ValueError("duplicate/empty instrument")
        if stamp(r["available_at"]) > first or r["kind"] not in {"bond", "etf"}:
            raise ValueError("unknown kind or future instrument master")
        integer(r["settlement_lag"], "settlement lag", 0, 10)
        if r["kind"] == "bond":
            b = Bond.parse(r["terms"])
            if b.instrument_id != ident or b.currency != raw["currency"]:
                raise ValueError("bond identity/currency mismatch")
            bonds[ident] = b
        elif "terms" in r:
            raise ValueError("ETF does not have bond terms")
        instruments[ident], scales[ident] = r, Decimal(100 if r["kind"] == "bond" else 1)
    if not instruments or (
        c["strategy"] == "allocation" and c["weights"].keys() - instruments.keys()
    ):
        raise ValueError("unknown allocation instrument")
    distributions = []
    seen = set()
    for d in raw["distributions"]:
        keys(d, ("instrument_id", "ex_date", "pay_date", "amount", "available_at"))
        ident, ex, pay = d["instrument_id"], day(d["ex_date"]), day(d["pay_date"])
        if ident not in instruments or ident in bonds or pay < ex:
            raise ValueError("invalid ETF distribution")
        if stamp(d["available_at"]).date() >= ex or ex <= first.date():
            raise ValueError("distribution must be declared before ex-date within replay window")
        if (ident, ex) in seen:
            raise ValueError("duplicate distribution")
        seen.add((ident, ex))
        positive(d["amount"], "distribution amount", zero=True)
        distributions.append(d)
    baskets, seen_baskets = [], set()
    for b in raw["baskets"]:
        keys(
            b, ("instrument_id", "observed_at", "available_at", "faces_per_share", "cash_per_share")
        )
        if b["instrument_id"] not in instruments or b["instrument_id"] in bonds:
            raise ValueError("unknown basket ETF")
        if stamp(b["observed_at"]) > stamp(b["available_at"]):
            raise ValueError("future basket observation")
        basket_key = b["instrument_id"], stamp(b["available_at"])
        if basket_key in seen_baskets:
            raise ValueError("duplicate basket publication")
        seen_baskets.add(basket_key)
        if not b["faces_per_share"] or b["faces_per_share"].keys() - bonds.keys():
            raise ValueError("basket contains unknown bonds")
        for v in b["faces_per_share"].values():
            positive(v, "basket face")
        number(b["cash_per_share"])
        baskets.append(b)
    cash = decimal(c["initial_cash"])
    settled = {k: Decimal(0) for k in instruments}
    pending, receivables, marks = [], [], {}
    ledger, fills, navs, positions, events, signals = [], [], [], [], [], []

    def post(date, amount, reason, ident=""):
        nonlocal cash
        cash += amount
        ledger.append(
            dict(
                datetime=str(date),
                currency=raw["currency"],
                instrument_id=ident,
                amount=str(amount),
                balance=str(cash),
                reason=reason,
            )
        )

    def economic():
        result = settled.copy()
        for t in pending:
            result[t["id"]] += t["units"]
        return result

    def mark(ident, date):
        if ident in bonds and date >= bonds[ident].periods[-1].payment:
            return Decimal(0)
        if ident not in marks:
            raise ValueError("missing instrument quote: " + ident)
        q = marks[ident]
        if (date - stamp(q["observed_at"]).date()).days > c["max_mark_age_days"]:
            raise ValueError("stale valuation quote: " + ident)
        value = (decimal(q["bid"]) + decimal(q["ask"])) / 2
        if ident in bonds:
            value += decimal(bonds[ident].accrued(date))
        return value / scales[ident]

    def value(date):
        v = cash + sum(r["amount"] for r in receivables)
        for ident, units in settled.items():
            if units:
                mv = units * mark(ident, date)
                v += mv
        for t in pending:
            mv = t["units"] * mark(t["id"], t["date"])
            v += t["cash"] + mv
        gross = sum(abs(units) * mark(ident, date) for ident, units in economic().items() if units)
        return v, gross

    post(first, Decimal(0), "initial_balance")
    ledger[0]["amount"] = str(cash)
    target = {k: Decimal(0) for k in instruments}
    previous = None
    for i, row in enumerate(rows):
        keys(
            row,
            (
                "observed_at",
                "available_at",
                "funding_rate",
                "rates_available_at",
                "quotes",
                "borrow",
            ),
        )
        now, obs = stamp(row["available_at"]), stamp(row["observed_at"])
        date = now.date()
        if obs > now or obs.date() != date or stamp(row["rates_available_at"]) > now:
            raise ValueError("invalid quote/funding chronology")
        number(row["funding_rate"])
        if previous:
            before = stamp(previous["available_at"])
            if (
                date <= before.date()
                or obs <= before
                or (date - before.date()).days > c["max_gap_days"]
            ):
                raise ValueError("noncausal or excessive observation gap")
            borrow_marks = {k: mark(k, before.date()) for k, v in economic().items() if v < 0}
            d = before.date()
            while d < date:
                d += timedelta(days=1)
                r = decimal(previous["funding_rate"]) + (
                    decimal(c["borrowing_spread"]) if cash < 0 else 0
                )
                post(d, cash * r / 365, "funding")
                for ident, units in economic().items():
                    if units >= 0:
                        continue
                    mv = abs(units) * borrow_marks[ident]
                    borrow = previous["borrow"].get(ident)
                    if borrow is None:
                        raise ValueError("missing borrow terms for held short")
                    post(d, -mv * decimal(borrow["rate"]) / 365, "borrow_fee", ident)
                for ident, bond in bonds.items():
                    for period in bond.periods:
                        if period.payment == d:
                            amount = (
                                settled[ident] * decimal(period.coupon + period.principal) / 100
                            )
                            post(d, amount, "coupon_principal", ident)
                            events.append(
                                dict(
                                    date=str(d),
                                    instrument_id=ident,
                                    kind="coupon_principal",
                                    amount=str(amount),
                                )
                            )
                            if period.principal:
                                settled[ident] = Decimal(0)
                                target[ident] = Decimal(0)
                for div in distributions:
                    if day(div["ex_date"]) == d:
                        if stamp(div["available_at"]) > before:
                            raise ValueError("distribution publication missing before event gap")
                        amount = economic()[div["instrument_id"]] * decimal(div["amount"])
                        receivables.append(
                            dict(date=day(div["pay_date"]), amount=amount, id=div["instrument_id"])
                        )
                        events.append(
                            dict(
                                date=str(d),
                                instrument_id=div["instrument_id"],
                                kind="dividend_entitlement",
                                amount=str(amount),
                            )
                        )
                for rec in list(receivables):
                    if rec["date"] == d:
                        post(d, rec["amount"], "dividend_payment", rec["id"])
                        receivables.remove(rec)
                for t in list(pending):
                    if t["date"] == d:
                        post(d, t["cash"], "trade_settlement", t["id"])
                        settled[t["id"]] += t["units"]
                        pending.remove(t)
        for ident, q in row["quotes"].items():
            keys(q, ("bid", "ask", "observed_at", "available_at", "kind"))
            if ident not in instruments or q["kind"] not in {"dealer", "exchange"}:
                raise ValueError("unknown instrument or non-executable quote kind")
            if stamp(q["observed_at"]) > stamp(q["available_at"]) or stamp(q["available_at"]) > now:
                raise ValueError("future individual quote")
            if positive(q["bid"]) > positive(q["ask"]):
                raise ValueError("crossed security quote")
            if ident in marks and stamp(q["observed_at"]) <= stamp(marks[ident]["observed_at"]):
                raise ValueError("duplicate or regressing security quote")
            marks[ident] = q
        for ident, b in row["borrow"].items():
            keys(b, ("available_at", "max_units", "rate"))
            if ident not in instruments or stamp(b["available_at"]) > now:
                raise ValueError("unknown/future borrow terms")
            positive(b["max_units"], "borrow capacity", zero=True)
            positive(b["rate"], "borrow rate", zero=True)
        before_units = economic()
        desired = (
            {k: Decimal(0) for k in instruments} if date >= day(c["close_on"]) else target.copy()
        )
        if i:
            for ident in instruments:
                delta = desired[ident] - before_units[ident]
                if abs(delta) < Decimal("0.00000001"):
                    continue
                q = row["quotes"].get(ident)
                if q is None or stamp(q["observed_at"]) <= stamp(previous["available_at"]):
                    raise ValueError("fill requires fresh quote after signal: " + ident)
                if (now - stamp(q["observed_at"])).total_seconds() > c[
                    "max_quote_age_hours"
                ] * 3600:
                    raise ValueError("execution quote too old")
                if desired[ident] < 0:
                    b = row["borrow"].get(ident)
                    if b is None or -desired[ident] > decimal(b["max_units"]):
                        raise ValueError("short not located or beyond borrow capacity")
                due = settlement(date, instruments[ident]["settlement_lag"], [calendar])
                px = decimal(q["ask"] if delta > 0 else q["bid"])
                px *= 1 + decimal(c["slippage_bps"]) / 10000 * (1 if delta > 0 else -1)
                accrued = decimal(bonds[ident].accrued(due)) if ident in bonds else Decimal(0)
                consideration = delta * (px + accrued) / scales[ident]
                fee = abs(consideration) * decimal(c["fee_bps"]) / 10000
                t = dict(id=ident, date=due, units=delta, cash=-consideration - fee)
                if due == date:
                    post(date, t["cash"], "trade_settlement", ident)
                    settled[ident] += delta
                else:
                    pending.append(t)
                fills.append(
                    dict(
                        datetime=row["available_at"],
                        instrument_id=ident,
                        units=str(delta),
                        clean_price=str(px),
                        accrued=str(accrued),
                        fee=str(fee),
                        cash=str(t["cash"]),
                        settlement=str(due),
                        signal_at=previous["available_at"],
                    )
                )
        current = economic()
        for ident, units in current.items():
            if units < 0:
                b = row["borrow"].get(ident)
                if b is None or -units > decimal(b["max_units"]):
                    raise ValueError(
                        "held short exceeds available borrow; explicit new window required"
                    )
        nav, gross = value(date)
        if nav <= 0 or gross > nav * decimal(c["max_leverage"]):
            raise ValueError("bond account leverage/solvency limit exceeded")
        navs.append(
            dict(
                datetime=row["available_at"],
                nav=str(nav),
                cash=str(cash),
                gross=str(gross),
                pending_trades=len(pending),
                dividend_receivable=str(sum(r["amount"] for r in receivables)),
            )
        )
        for ident in instruments:
            positions.append(
                dict(
                    datetime=row["available_at"],
                    instrument_id=ident,
                    settled=str(settled[ident]),
                    pending=str(current[ident] - settled[ident]),
                    mark_kind="observed_or_carried_mid",
                )
            )
        if date < day(c["close_on"]):
            if c["strategy"] == "allocation":
                if i == 0 or c["reinvest"]:
                    target = {k: Decimal(0) for k in instruments}
                    for ident, weight in c["weights"].items():
                        px = mark(ident, date)
                        if px:
                            target[ident] = nav * decimal(weight) / px
            else:
                candidates = [
                    b
                    for b in baskets
                    if b["instrument_id"] == c["etf_id"] and stamp(b["available_at"]) <= now
                ]
                if not candidates:
                    raise ValueError("no PIT ETF basket for signal")
                basket = max(candidates, key=lambda b: stamp(b["available_at"]))
                if (date - stamp(basket["observed_at"]).date()).days > c["max_mark_age_days"]:
                    raise ValueError("stale ETF basket")
                basket_value = decimal(basket["cash_per_share"]) + sum(
                    decimal(face) * mark(k, date) for k, face in basket["faces_per_share"].items()
                )
                if basket_value <= 0:
                    raise ValueError("nonpositive basket")
                premium = (mark(c["etf_id"], date) / basket_value - 1) * 10000
                direction = (
                    -1 if premium > c["entry_bps"] else 1 if premium < -c["entry_bps"] else 0
                )
                target = {k: Decimal(0) for k in instruments}
                target[c["etf_id"]] = decimal(c["etf_units"]) * direction
                for ident, face in basket["faces_per_share"].items():
                    target[ident] = -target[c["etf_id"]] * decimal(face)
                signals.append(
                    dict(
                        datetime=row["available_at"],
                        etf=c["etf_id"],
                        premium_bps=str(premium),
                        basket_value=str(basket_value),
                        direction=direction,
                        basket_available_at=basket["available_at"],
                    )
                )
        previous = row
    if not first.date() < day(c["close_on"]) <= stamp(rows[-1]["available_at"]).date():
        raise ValueError("close_on outside replay window")
    if pending or receivables or any(abs(v) > Decimal("0.00000001") for v in settled.values()):
        raise ValueError("window ends with unsettled trades, distributions or open holdings")
    return dict(
        nav=navs, fills=fills, ledger=ledger, positions=positions, cashflows=events, signals=signals
    )
