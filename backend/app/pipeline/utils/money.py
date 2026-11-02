"""Decimal money parsing utilities. Implemented in M1."""
from __future__ import annotations

from decimal import Decimal


def parse_amount(value: str, *, decimal_sep: str = ".", thousands_sep: str = ",") -> Decimal:
    """Parse a printed amount string to Decimal. Raises ValueError on failure."""
    raise NotImplementedError("Implemented in M1")


def amounts_equal(a: Decimal, b: Decimal, tol: Decimal = Decimal("0.01")) -> bool:
    """Return True if |a - b| <= tol."""
    raise NotImplementedError("Implemented in M1")
