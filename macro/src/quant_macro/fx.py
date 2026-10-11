"""Causal two-currency research accounts and cash-settled rolling forwards.

Pair convention: quote currency per one base. Signals use the previous available
observation; fills use the next observed bid/ask. Forward MTM requires an observed
quote for the *same* maturity; maturity settlement requires an explicit fixing.
"""

from decimal import Decimal

from .common import currency, day, decimal, integer, keys, number, positive, stamp


def forward_price(spot, base_rate, quote_rate, base_years, quote_years=None):
    """Covered interest parity with simple, explicitly supplied year fractions."""
    tb = positive(base_years, "base years", zero=True)
    tq = tb if quote_years is None else positive(quote_years, "quote years", zero=True)
    base = 1 + number(base_rate) * tb
    quote = 1 + number(quote_rate) * tq
    if base <= 0 or quote <= 0:
        raise ValueError("nonpositive funding factor")
    return positive(spot) * quote / base


DEFAULTS = {
    "line": "fx",
    "instrument": "spot",
    "strategy": "carry",
    "initial_cash": 100000,
    "notional_base": 10000,
    "fee_bps": 1,
    "slippage_bps": 0,
    "borrowing_spread": 0,
    "lookback": 5,
    "max_leverage": 2,
    "max_quote_age_hours": 24,
    "max_gap_days": 7,
}


def configuration(raw):
    keys(raw, (), DEFAULTS)
    c = DEFAULTS | raw
    if c["line"] != "fx" or c["instrument"] not in {"spot", "forward"}:
        raise ValueError("fx spot or forward required")
    if c["strategy"] not in {"carry", "momentum", "long"}:
        raise ValueError("strategy must be carry, momentum or long")
    for key in ("initial_cash", "notional_base", "max_leverage", "max_gap_days"):
        c[key] = positive(c[key], key)
    for key in ("fee_bps", "slippage_bps", "borrowing_spread", "max_quote_age_hours"):
        c[key] = positive(c[key], key, zero=True)
    if c["fee_bps"] >= 10000 or c["slippage_bps"] >= 10000:
        raise ValueError("cost in bps must be below 10000")
    c["lookback"] = integer(c["lookback"], "lookback", 1, 10000)
    return c


def validate(raw, c):
    keys(raw, ("base", "quote", "base_rate_basis", "quote_rate_basis", "quotes"))
    if currency(raw["base"]) == currency(raw["quote"]):
        raise ValueError("FX pair currencies must differ")
    for key in ("base_rate_basis", "quote_rate_basis"):
        if raw[key] not in {360, 365}:
            raise ValueError("rate basis must be 360 or 365")
    if not isinstance(raw["quotes"], list) or len(raw["quotes"]) < 3:
        raise ValueError("FX replay needs at least three observations")
    previous = None
    for row in raw["quotes"]:
        keys(
            row,
            (
                "observed_at",
                "available_at",
                "bid",
                "ask",
                "base_rate",
                "quote_rate",
                "rates_available_at",
            ),
            ("forwards", "fixing"),
        )
        observed, available = stamp(row["observed_at"]), stamp(row["available_at"])
        if observed > available or stamp(row["rates_available_at"]) > available:
            raise ValueError("FX data/rates not available at observation event")
        if (available - observed).total_seconds() > c["max_quote_age_hours"] * 3600:
            raise ValueError("stale FX quote")
        if previous:
            prev_observed, prev_available = previous
            if observed <= prev_observed or available <= prev_available:
                raise ValueError("FX observations must strictly increase without duplicates")
            if observed <= prev_available:
                raise ValueError(
                    "next executable quote must be observed after prior signal availability"
                )
            if (available - prev_available).total_seconds() > c["max_gap_days"] * 86400:
                raise ValueError("FX observation gap exceeds configured bound")
        previous = observed, available
        if positive(row["bid"], "bid") > positive(row["ask"], "ask"):
            raise ValueError("crossed FX quote")
        number(row["base_rate"])
        number(row["quote_rate"])
        maturities = set()
        for f in row.get("forwards", []):
            keys(f, ("maturity", "bid", "ask"))
            maturity = day(f["maturity"])
            if maturity <= available.date() or maturity in maturities:
                raise ValueError("forward maturity must be future and unique")
            maturities.add(maturity)
            if positive(f["bid"]) > positive(f["ask"]):
                raise ValueError("crossed forward quote")
        if row.get("fixing") is not None:
            positive(row["fixing"], "fixing")
    return raw


def _signal(rows, previous_index, c):
    if c["strategy"] == "long":
        return 1
    if c["strategy"] == "carry":
        previous = rows[previous_index]
        # Compare annualized simple rates on a common ACT/365 horizon.
        value = previous["base_rate_365"] - previous["quote_rate_365"]
    else:
        if previous_index < c["lookback"]:
            return 0
        previous, older = rows[previous_index], rows[previous_index - c["lookback"]]
        value = number(previous["bid"]) + number(previous["ask"])
        value -= number(older["bid"]) + number(older["ask"])
    return (value > 0) - (value < 0)


def replay(raw, raw_config):
    c = configuration(raw_config)
    validate(raw, c)
    rows = [
        r
        | {
            "base_rate_365": number(r["base_rate"]) * 365 / raw["base_rate_basis"],
            "quote_rate_365": number(r["quote_rate"]) * 365 / raw["quote_rate_basis"],
        }
        for r in raw["quotes"]
    ]
    base, quote = raw["base"], raw["quote"]
    balances = {base: Decimal(0), quote: decimal(c["initial_cash"])}
    navs, fills, ledger, positions, forwards = [], [], [], [], []
    outstanding = None

    def post(at, ccy, amount, reason):
        balances[ccy] += amount
        ledger.append(
            {
                "timestamp": at,
                "currency": ccy,
                "amount": str(amount),
                "balance": str(balances[ccy]),
                "reason": reason,
            }
        )

    # Include opening balances so every currency ledger reconciles from zero.
    ledger.append(
        {
            "timestamp": rows[0]["available_at"],
            "currency": quote,
            "amount": str(balances[quote]),
            "balance": str(balances[quote]),
            "reason": "initial_cash",
        }
    )
    for i, row in enumerate(rows):
        at, now = row["available_at"], stamp(row["available_at"])
        mid = (decimal(row["bid"]) + decimal(row["ask"])) / 2
        available_forwards = {f["maturity"]: f for f in row.get("forwards", [])}
        if i:
            prev = rows[i - 1]
            days = decimal((now - stamp(prev["available_at"])).total_seconds()) / 86400
            for ccy, field, basis in (
                (base, "base_rate", raw["base_rate_basis"]),
                (quote, "quote_rate", raw["quote_rate_basis"]),
            ):
                rate = decimal(prev[field])
                if balances[ccy] < 0:
                    rate += decimal(c["borrowing_spread"])
                factor = rate * days / basis
                if factor <= -1:
                    raise ValueError("nonpositive cash accrual factor")
                post(at, ccy, balances[ccy] * factor, "interest")

        if outstanding and day(outstanding["maturity"]) <= now.date():
            if day(outstanding["maturity"]) != now.date() or row.get("fixing") is None:
                raise ValueError("explicit fixing at exact forward maturity required")
            pnl = outstanding["quantity"] * (decimal(row["fixing"]) - outstanding["strike"])
            post(at, quote, pnl, "forward_settlement")
            forwards.append(
                {
                    "timestamp": at,
                    "event": "settle",
                    "maturity": outstanding["maturity"],
                    "pnl": str(pnl),
                }
            )
            outstanding = None

        signal = _signal(rows, i - 1, c) if i else 0
        # Liquidate spot at the final quote; never create a final-row derivative.
        target = decimal(c["notional_base"]) * signal if i < len(rows) - 1 else Decimal(0)
        before = balances[quote] + balances[base] * mid
        if before <= 0:
            raise ValueError("nonpositive account NAV")
        if abs(target * mid) > before * decimal(c["max_leverage"]):
            raise ValueError("target exceeds research leverage cap")
        if c["instrument"] == "spot":
            delta = target - balances[base]
            if delta:
                execution = decimal(row["ask"] if delta > 0 else row["bid"])
                execution *= 1 + decimal(c["slippage_bps"]) / 10000 * (1 if delta > 0 else -1)
                fee = abs(delta * execution) * decimal(c["fee_bps"]) / 10000
                post(at, base, delta, "spot_trade")
                post(at, quote, -delta * execution, "spot_trade")
                post(at, quote, -fee, "fee")
                fills.append(
                    {
                        "timestamp": at,
                        "signal_at": rows[i - 1]["available_at"],
                        "instrument_id": f"{base}/{quote}",
                        "quantity": str(delta),
                        "price": str(execution),
                        "fee": str(fee),
                        "currency": quote,
                    }
                )
        elif outstanding is None and target:
            eligible = sorted(
                f for f in available_forwards if day(f) <= stamp(rows[-1]["available_at"]).date()
            )
            if not eligible:
                raise ValueError("no forward maturity covered by replay horizon")
            maturity = eligible[0]
            forward = available_forwards[maturity]
            strike = decimal(forward["ask"] if target > 0 else forward["bid"])
            strike *= 1 + decimal(c["slippage_bps"]) / 10000 * (1 if target > 0 else -1)
            fee = abs(target * strike) * decimal(c["fee_bps"]) / 10000
            post(at, quote, -fee, "fee")
            outstanding = {"maturity": maturity, "strike": strike, "quantity": target}
            fills.append(
                {
                    "timestamp": at,
                    "signal_at": rows[i - 1]["available_at"],
                    "instrument_id": f"{base}/{quote}:{maturity}",
                    "quantity": str(target),
                    "price": str(strike),
                    "fee": str(fee),
                    "currency": quote,
                }
            )
            forwards.append({"timestamp": at, "event": "open", "maturity": maturity, "pnl": "0"})

        mtm = Decimal(0)
        if outstanding:
            mark = available_forwards.get(outstanding["maturity"])
            if mark is None:
                raise ValueError("same-maturity observed forward quote required for MTM")
            forward_mid = (decimal(mark["bid"]) + decimal(mark["ask"])) / 2
            days = decimal((day(outstanding["maturity"]) - now.date()).days)
            discount = 1 + decimal(row["quote_rate"]) * days / raw["quote_rate_basis"]
            if discount <= 0:
                raise ValueError("nonpositive forward discount denominator")
            mtm = outstanding["quantity"] * (forward_mid - outstanding["strike"]) / discount
        nav = balances[quote] + balances[base] * mid + mtm
        if nav <= 0:
            raise ValueError("nonpositive account NAV after costs/marking")
        gross = abs((outstanding["quantity"] if outstanding else balances[base]) * mid)
        if gross > nav * decimal(c["max_leverage"]):
            raise ValueError("marked exposure exceeds research leverage cap")
        navs.append(
            {
                "datetime": at,
                "timestamp": at,
                "nav": str(nav),
                "currency": quote,
                "base_cash": str(balances[base]),
                "quote_cash": str(balances[quote]),
                "fx_mid": str(mid),
                "forward_mtm": str(mtm),
            }
        )
        positions.append(
            {
                "timestamp": at,
                "base_currency": base,
                "quote_currency": quote,
                "base_cash": str(balances[base]),
                "quote_cash": str(balances[quote]),
                "forward_quantity": str(outstanding["quantity"] if outstanding else 0),
            }
        )
    if outstanding:
        raise ValueError("unsettled forward at end of research window")
    return {
        "nav": navs,
        "fills": fills,
        "ledger": ledger,
        "positions": positions,
        "rolls": forwards,
    }
