"""Strict, deterministic boundaries shared by the research lines."""

import json
import math
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation


def number(value, name="number"):
    if isinstance(value, bool):
        raise ValueError(f"{name}: boolean is not a number")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name}: finite number required") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name}: finite number required")
    return result


def positive(value, name="number", zero=False):
    result = number(value, name)
    if result < 0 or (not zero and result == 0):
        raise ValueError(f"{name}: {'nonnegative' if zero else 'positive'} required")
    return result


def decimal(value):
    number(value)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc


def integer(value, name, low, high):
    result = number(value, name)
    if result != int(result) or not low <= result <= high:
        raise ValueError(f"{name}: integer in [{low}, {high}] required")
    return int(result)


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string with timezone")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(UTC)


def day(value):
    if not isinstance(value, str):
        raise ValueError("ISO date required")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("canonical ISO date required")
    return result


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("three-letter currency required")
    return value


def keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError("object required")
    if missing := set(required) - value.keys():
        raise ValueError(f"missing fields: {sorted(missing)}")
    if extra := value.keys() - set(required) - set(optional):
        raise ValueError(f"unknown fields: {sorted(extra)}")


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: nonempty text required")
    return value


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"non-finite JSON: {value}")

    return json.loads(
        path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid
    )
