"""Uncertified multi-pair spot research with shared currency balances and T+n settlement.

Daily convention: fund prior settled cash, settle due trades, then execute at the
next published quote. Pending principal is part of NAV but does not earn interest.
"""

from datetime import timedelta
from decimal import Decimal

from .calendars import Calendar, settlement
from .common import currency, day, decimal, integer, keys, number, positive, stamp

DEFAULTS = {
    "line": "fx_portfolio",
    "reporting_currency": "USD",
    "initial_cash": 100000,
    "strategy": "carry",
    "lookback": 3,
    "fee_bps": 1,
    "slippage_bps": 0,
    "borrowing_spread": 0.005,
    "max_leverage": 2,
    "max_gap_days": 7,
    "max_quote_age_hours": 24,
    "close_on": "2026-01-12",
}


def configuration(raw):
    keys(raw, (), DEFAULTS)
    c = DEFAULTS | raw
    if c["line"] != "fx_portfolio" or c["strategy"] not in {"carry", "momentum", "long"}:
        raise ValueError("invalid portfolio line/strategy")
    currency(c["reporting_currency"])
    day(c["close_on"])
    c["lookback"] = integer(c["lookback"], "lookback", 1, 10000)
    for key in ("initial_cash", "max_leverage", "max_gap_days"):
        c[key] = positive(c[key], key)
    for key in ("fee_bps", "slippage_bps", "borrowing_spread", "max_quote_age_hours"):
        c[key] = positive(c[key], key, zero=True)
    if max(c["fee_bps"], c["slippage_bps"]) >= 10000:
        raise ValueError("cost must be below 10000 bps")
    return c


def conversions(pairs, quotes, reporting):
    """Connected mid-quote tree with cross-rate validation; unit is report/ccy."""
    result = {reporting: Decimal(1)}
    for _ in range(len(pairs) + 1):
        for ident, p in pairs.items():
            mid = (decimal(quotes[ident]["bid"]) + decimal(quotes[ident]["ask"])) / 2
            b, q = p["base"], p["quote"]
            if q in result and b not in result:
                result[b] = mid * result[q]
            elif b in result and q not in result:
                result[q] = result[b] / mid
    currencies = {p[k] for p in pairs.values() for k in ("base", "quote")}
    if currencies - result.keys():
        raise ValueError("FX graph is disconnected from reporting currency")
    for ident, p in pairs.items():
        cross = result[p["base"]] / result[p["quote"]]
        q = quotes[ident]
        if not decimal(q["bid"]) <= cross <= decimal(q["ask"]):
            raise ValueError("inconsistent FX cross rate outside supplied bid/ask")
    return result


def replay(raw, raw_config):
    c = configuration(raw_config)
    keys(raw, ("currencies", "pairs", "observations"))
    calendars, bases = {}, {}
    for ccy, terms in raw["currencies"].items():
        currency(ccy)
        keys(terms, ("calendar", "rate_basis"))
        calendars[ccy] = Calendar(terms["calendar"])
        if terms["rate_basis"] not in (360, 365):
            raise ValueError("rate basis must be 360 or 365")
        bases[ccy] = terms["rate_basis"]
    pairs = {}
    for p in raw["pairs"]:
        keys(p, ("instrument_id", "base", "quote", "notional_base", "settlement_lag"))
        ident = p["instrument_id"]
        if not isinstance(ident, str) or not ident or ident in pairs:
            raise ValueError("duplicate/empty pair id")
        if p["base"] == p["quote"] or {p["base"], p["quote"]} - calendars.keys():
            raise ValueError("invalid pair currencies")
        positive(p["notional_base"], "notional")
        integer(p["settlement_lag"], "lag", 0, 10)
        pairs[ident] = p
    rows = raw["observations"]
    if not pairs or len(rows) < 3 or c["reporting_currency"] not in calendars:
        raise ValueError("pairs, reporting currency and at least three observations required")
    close = day(c["close_on"])
    cash = {ccy: Decimal(0) for ccy in calendars}
    positions = {ident: Decimal(0) for ident in pairs}
    pending, navs, fills, ledger, snapshots, settlements = [], [], [], [], [], []

    def post(at, ccy, amount, reason):
        cash[ccy] += amount
        ledger.append(
            dict(
                datetime=str(at),
                currency=ccy,
                amount=str(amount),
                balance=str(cash[ccy]),
                reason=reason,
            )
        )

    post(rows[0]["available_at"], c["reporting_currency"], decimal(c["initial_cash"]), "initial")
    previous = None
    for i, row in enumerate(rows):
        keys(row, ("observed_at", "available_at", "rates_available_at", "rates", "quotes"))
        observed, now = stamp(row["observed_at"]), stamp(row["available_at"])
        date = now.date()
        if observed > now or observed.date() != date:
            raise ValueError("daily FX quote observed date must equal event date")
        if stamp(row["rates_available_at"]) > now:
            raise ValueError("future funding rate publication")
        if (now - observed).total_seconds() > c["max_quote_age_hours"] * 3600:
            raise ValueError("stale FX portfolio quote")
        if set(row["rates"]) != set(cash) or set(row["quotes"]) != set(pairs):
            raise ValueError("missing/extra currency rate or pair quote")
        for rate in row["rates"].values():
            number(rate, "rate")
        for q in row["quotes"].values():
            keys(q, ("bid", "ask"))
            if positive(q["bid"]) > positive(q["ask"]):
                raise ValueError("crossed quote")
        rates = conversions(pairs, row["quotes"], c["reporting_currency"])
        if set(rates) != set(cash):
            raise ValueError("every account currency needs a connected FX valuation")
        if previous:
            before = stamp(previous["available_at"])
            if date <= before.date() or observed <= before:
                raise ValueError("execution must follow prior signal publication on a later date")
            if (date - before.date()).days > c["max_gap_days"]:
                raise ValueError("portfolio observation gap too large")
            d = before.date()
            while d < date:
                d += timedelta(days=1)
                for ccy in cash:
                    r = decimal(previous["rates"][ccy])
                    if cash[ccy] < 0:
                        r += decimal(c["borrowing_spread"])
                    post(d, ccy, cash[ccy] * r / bases[ccy], "funding")
                for trade in list(pending):
                    if trade["date"] == d:
                        for ccy, amount in trade["legs"].items():
                            post(d, ccy, amount, "settlement:" + trade["id"])
                            settlements.append(
                                dict(
                                    date=str(d),
                                    instrument_id=trade["id"],
                                    currency=ccy,
                                    amount=str(amount),
                                )
                            )
                        pending.remove(trade)
        target = positions.copy()
        if i:
            for ident, p in pairs.items():
                direction = 0
                if date < close:
                    if c["strategy"] == "long":
                        direction = 1
                    elif c["strategy"] == "carry":
                        spread = number(previous["rates"][p["base"]]) * 365 / bases[p["base"]]
                        spread -= number(previous["rates"][p["quote"]]) * 365 / bases[p["quote"]]
                        direction = (spread > 0) - (spread < 0)
                    elif i > c["lookback"]:
                        a = previous["quotes"][ident]
                        b = rows[i - 1 - c["lookback"]]["quotes"][ident]
                        diff = (
                            number(a["bid"])
                            + number(a["ask"])
                            - number(b["bid"])
                            - number(b["ask"])
                        )
                        direction = (diff > 0) - (diff < 0)
                target[ident] = decimal(p["notional_base"]) * direction
        for ident, p in pairs.items():
            delta = target[ident] - positions[ident]
            if not delta:
                continue
            due = settlement(
                date, p["settlement_lag"], [calendars[p["base"]], calendars[p["quote"]]]
            )
            q = row["quotes"][ident]
            price = decimal(q["ask"] if delta > 0 else q["bid"])
            price *= 1 + decimal(c["slippage_bps"]) / 10000 * (1 if delta > 0 else -1)
            fee = abs(delta * price) * decimal(c["fee_bps"]) / 10000
            legs = {p["base"]: delta, p["quote"]: -delta * price - fee}
            if due == date:
                for ccy, amount in legs.items():
                    post(date, ccy, amount, "settlement:" + ident)
                    settlements.append(
                        dict(date=str(date), instrument_id=ident, currency=ccy, amount=str(amount))
                    )
            else:
                pending.append(dict(date=due, id=ident, legs=legs))
            positions[ident] = target[ident]
            fills.append(
                dict(
                    datetime=row["available_at"],
                    instrument_id=ident,
                    units=str(delta),
                    price=str(price),
                    fee=str(fee),
                    settlement=str(due),
                    signal_at=previous["available_at"],
                )
            )
        economic = cash.copy()
        for trade in pending:
            for ccy, amount in trade["legs"].items():
                economic[ccy] += amount
        nav = sum(economic[k] * rates[k] for k in cash)
        gross = sum(abs(v) * rates[pairs[k]["base"]] for k, v in positions.items())
        currency_gross = sum(
            abs(economic[k] * rates[k]) for k in cash if k != c["reporting_currency"]
        )
        if nav <= 0 or max(gross, currency_gross) > nav * decimal(c["max_leverage"]):
            raise ValueError("FX portfolio leverage/solvency limit exceeded")
        for ccy in cash:
            snapshots.append(
                dict(
                    datetime=row["available_at"],
                    currency=ccy,
                    settled=str(cash[ccy]),
                    pending=str(economic[ccy] - cash[ccy]),
                    rate=str(rates[ccy]),
                    value=str(economic[ccy] * rates[ccy]),
                )
            )
        navs.append(
            dict(
                datetime=row["available_at"],
                nav=str(nav),
                cash=str(cash[c["reporting_currency"]]),
                gross=str(gross),
                pending_trades=len(pending),
            )
        )
        previous = row
    if day(rows[0]["available_at"][:10]) >= close or close > stamp(rows[-1]["available_at"]).date():
        raise ValueError("close_on must lie after start and within observation window")
    if pending or any(positions.values()):
        raise ValueError("window ends with unsettled trades or open pair positions")
    return dict(nav=navs, fills=fills, ledger=ledger, positions=snapshots, settlements=settlements)
