"""Explicit separator tests for parse_amount — M4."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.utils.money import parse_amount


def test_eu_locale_decimal_comma():
    assert parse_amount("1.234,56", decimal_sep=",", thousands_sep=".") == Decimal("1234.56")


def test_eu_locale_no_thousands():
    assert parse_amount("1.234", decimal_sep=",", thousands_sep=".") == Decimal("1234")


def test_us_locale_no_decimal():
    assert parse_amount("1,234", decimal_sep=".", thousands_sep=",") == Decimal("1234")


def test_us_locale_with_decimal():
    assert parse_amount("1,234.56", decimal_sep=".", thousands_sep=",") == Decimal("1234.56")


def test_defaults_unchanged_no_decimal():
    assert parse_amount("1,234") == Decimal("1234")


def test_defaults_unchanged_with_decimal():
    assert parse_amount("1,234.56") == Decimal("1234.56")


def test_usage_addition():
    from app.pipeline.llm.client import Usage
    assert Usage(1, 2, 3) + Usage(4, 5, 6) == Usage(5, 7, 9)
