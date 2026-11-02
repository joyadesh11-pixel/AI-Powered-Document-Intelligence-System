"""Date parsing utilities."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime


_MONTH_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}

_MON_PATTERN = "|".join(sorted(_MONTH_MAP, key=len, reverse=True))
_DAY_FIRST_RE = re.compile(
    rf"(\d{{1,2}})[\s\-/]+({_MON_PATTERN})[\s\-/]+(\d{{4}})",
    re.IGNORECASE,
)
_MON_FIRST_RE = re.compile(
    rf"({_MON_PATTERN})[\s\-/]+(\d{{1,2}}),?\s+(\d{{4}})",
    re.IGNORECASE,
)


@dataclass
class ParsedDate:
    value: date
    ambiguous: bool = False


def parse_date(
    value: str,
    fmt: str | None = None,
    day_first: bool | None = None,
) -> ParsedDate:
    """Parse a date string. Returns ParsedDate(value, ambiguous).

    ambiguous=True when day/month order was guessed (no hint, no unambiguous digit > 12).
    Raises ValueError if all strategies fail.
    """
    s = value.strip()
    if not s:
        raise ValueError("empty date string")

    # Explicit format
    if fmt:
        return ParsedDate(datetime.strptime(s, fmt).date(), ambiguous=False)

    # ISO YYYY-MM-DD
    try:
        return ParsedDate(date.fromisoformat(s), ambiguous=False)
    except ValueError:
        pass

    # Day-first named month: "3 Nov 2026", "3-Nov-2026"
    m = _DAY_FIRST_RE.search(s)
    if m:
        day, mon_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = _MONTH_MAP.get(mon_str)
        if month:
            return ParsedDate(date(year, month, day), ambiguous=False)

    # Month-first named: "November 3, 2026", "Nov 3 2026"
    m = _MON_FIRST_RE.search(s)
    if m:
        mon_str, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        month = _MONTH_MAP.get(mon_str)
        if month:
            return ParsedDate(date(year, month, day), ambiguous=False)

    # Numeric with 4-digit year
    parts = re.findall(r"\d+", s)
    if len(parts) >= 3:
        year_idx = next((i for i, p in enumerate(parts) if len(p) == 4), None)
        if year_idx is not None:
            year = int(parts[year_idx])
            rest = [int(parts[i]) for i in range(len(parts)) if i != year_idx][:2]
            if len(rest) == 2:
                a, b = rest
                result = _resolve_day_month(a, b, year, day_first)
                if result:
                    return result

    # Two-digit year (pattern like DD/MM/YY or MM/DD/YY)
    m2 = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})", s)
    if m2:
        a, b, yy = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        year = (2000 + yy) if yy < 70 else (1900 + yy)
        result = _resolve_day_month(a, b, year, day_first)
        if result:
            return result

    raise ValueError(f"Cannot parse date: {value!r}")


def _resolve_day_month(
    a: int, b: int, year: int, day_first: bool | None
) -> ParsedDate | None:
    if a > 12:
        day, month = a, b
        ambiguous = False
    elif b > 12:
        day, month = b, a
        ambiguous = False
    elif day_first is not None:
        day, month = (a, b) if day_first else (b, a)
        ambiguous = False
    else:
        day, month = a, b  # default day-first
        ambiguous = True
    try:
        return ParsedDate(date(year, month, day), ambiguous=ambiguous)
    except ValueError:
        return None
