"""Supplied settlement calendars; no silent guess of exchange holidays."""

from datetime import timedelta

from .common import day, integer, keys, nonempty


class Calendar:
    def __init__(self, raw):
        keys(raw, ("start", "end", "holidays", "source"))
        self.start, self.end = day(raw["start"]), day(raw["end"])
        nonempty(raw["source"], "calendar source")
        self.holidays = {day(x) for x in raw["holidays"]}
        if self.end < self.start or len(self.holidays) != len(raw["holidays"]):
            raise ValueError("invalid calendar bounds or duplicate holiday")
        if any(not self.start <= x <= self.end for x in self.holidays):
            raise ValueError("holiday outside calendar coverage")

    def business(self, date):
        if not self.start <= date <= self.end:
            raise ValueError("date outside explicit calendar coverage")
        return date.weekday() < 5 and date not in self.holidays


def settlement(trade_date, lag, calendars):
    remaining = integer(lag, "settlement lag", 0, 10)
    date = trade_date
    if not calendars or not all(c.business(date) for c in calendars):
        raise ValueError("trade date must be a joint business day")
    while remaining:
        date += timedelta(days=1)
        if all(c.business(date) for c in calendars):
            remaining -= 1
    return date
