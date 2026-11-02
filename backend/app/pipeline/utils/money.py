"""Decimal money parsing utilities."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


_CURRENCY_SYMBOLS = re.compile(r'^[$£€¥]\s*|\s*[$£€¥]$')
_CURRENCY_CODES = re.compile(r'^[A-Z]{3}\s+|\s+[A-Z]{3}$')
_SPACES_APOS = re.compile(r"[\s']")


def parse_amount(value: str, *, decimal_sep: str = ".", thousands_sep: str = ",") -> Decimal | None:
    """Parse a printed amount string to Decimal. Returns None on failure."""
    s = value.strip()
    if not s:
        return None

    # Detect negative
    negative = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
        negative = True
    elif s.startswith("-"):
        s = s[1:].strip()
        negative = True
    elif s.endswith("-"):
        s = s[:-1].strip()
        negative = True

    # Strip currency symbols and 3-letter codes
    s = _CURRENCY_SYMBOLS.sub("", s).strip()
    s = _CURRENCY_CODES.sub("", s).strip()

    # Strip spaces and apostrophes (thousands grouping)
    s = _SPACES_APOS.sub("", s)

    if not s:
        return None

    dots = [i for i, c in enumerate(s) if c == "."]
    commas = [i for i, c in enumerate(s) if c == ","]

    if dots and commas:
        # Both types present: last one determines decimal
        if max(dots) > max(commas):
            cleaned = s.replace(",", "")
        else:
            cleaned = s.replace(".", "").replace(",", ".")
    elif commas:
        if len(commas) > 1:
            # Multiple commas → all thousands, no decimal
            cleaned = s.replace(",", "")
        else:
            trailing = s[commas[0] + 1:]
            if re.fullmatch(r"\d{1,2}", trailing):
                # 1–2 digits after comma → decimal
                cleaned = s.replace(",", ".")
            elif re.fullmatch(r"\d{3}", trailing):
                # 3 digits → use explicit thousands_sep to decide
                if thousands_sep == ",":
                    cleaned = s.replace(",", "")
                else:
                    cleaned = s.replace(",", ".")
            else:
                # 0 or 4+ digits → treat comma as thousands
                cleaned = s.replace(",", "")
    elif dots:
        if len(dots) > 1:
            # Multiple dots → all thousands, no decimal
            cleaned = s.replace(".", "")
        else:
            trailing = s[dots[0] + 1:]
            if re.fullmatch(r"\d{1,2}", trailing):
                cleaned = s
            elif re.fullmatch(r"\d{3}", trailing):
                # 3 digits → use explicit decimal_sep to decide
                if decimal_sep == ".":
                    cleaned = s  # dot is decimal
                else:
                    cleaned = s.replace(".", "")
            else:
                cleaned = s
    else:
        cleaned = s

    try:
        result = Decimal(cleaned)
    except InvalidOperation:
        return None

    return -result if negative else result


def amounts_equal(a: Decimal, b: Decimal, tol: Decimal = Decimal("0.01")) -> bool:
    """Return True if |a - b| <= tol."""
    return abs(a - b) <= tol
