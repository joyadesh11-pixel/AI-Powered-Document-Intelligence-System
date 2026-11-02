"""Format checks: bad numbers, bad dates, date order/range, bad currency, negatives."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.pipeline.utils.dates import parse_date
from app.schemas.models import Issue, IssueCode, Severity


# Bundled ISO 4217 codes (common subset; extend as needed)
_ISO_4217 = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BMD", "BND", "BOB", "BRL", "BSD",
    "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY", "COP",
    "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP", "ERN",
    "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GMD", "GNF", "GTQ",
    "GYD", "HKD", "HNL", "HRK", "HTG", "HUF", "IDR", "ILS", "INR", "IQD",
    "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF", "KPW",
    "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL", "LYD",
    "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR", "MVR",
    "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR", "NZD",
    "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG", "QAR", "RON",
    "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK", "SGD", "SHP",
    "SLL", "SOS", "SRD", "STN", "SVC", "SYP", "SZL", "THB", "TJS", "TMT",
    "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX", "USD", "UYU",
    "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XOF", "XPF", "YER",
    "ZAR", "ZMW", "ZWL",
})

_NUMERIC_RAW_FIELDS = ("subtotal", "tax", "total")
_DATE_RAW_FIELDS = ("invoice_date", "due_date")
_DATE_MIN = date(2000, 1, 1)
_DATE_MAX_DAYS_AHEAD = 365


def check_formats(invoice: Any, *, date_min: date | None = None,
                  date_max_days_ahead: int | None = None) -> list[Issue]:
    issues: list[Issue] = []
    raw = _get(invoice, "raw") or {}
    dm = date_min or _DATE_MIN
    dmax_days = date_max_days_ahead if date_max_days_ahead is not None else _DATE_MAX_DAYS_AHEAD

    # BAD_NUMBER: non-empty raw string AND parsed field is None
    for f in _NUMERIC_RAW_FIELDS:
        raw_val = raw.get(f)
        parsed = _get(invoice, f)
        if raw_val and str(raw_val).strip() and parsed is None:
            issues.append(Issue(
                code=IssueCode.BAD_NUMBER,
                severity=Severity.error,
                field=f,
                got=str(raw_val),
                message=f"Field '{f}' raw value {raw_val!r} could not be parsed as a number",
            ))

    # BAD_NUMBER for line item amounts
    line_items = _get(invoice, "line_items") or []
    for i, li in enumerate(line_items):
        li_raw = _get(li, "raw") or {}
        for sub in ("quantity", "unit_price", "amount"):
            rv = li_raw.get(sub)
            pv = _get(li, sub)
            if rv and str(rv).strip() and pv is None:
                issues.append(Issue(
                    code=IssueCode.BAD_NUMBER,
                    severity=Severity.error,
                    field=f"line_items[{i}].{sub}",
                    got=str(rv),
                    message=f"Line item {i} field '{sub}' could not be parsed",
                ))

    # BAD_DATE: non-empty raw string AND parsed date field is None
    for f in _DATE_RAW_FIELDS:
        raw_val = raw.get(f)
        parsed = _get(invoice, f)
        if raw_val and str(raw_val).strip() and parsed is None:
            issues.append(Issue(
                code=IssueCode.BAD_DATE,
                severity=Severity.error,
                field=f,
                got=str(raw_val),
                message=f"Field '{f}' raw value {raw_val!r} could not be parsed as a date",
            ))

    # UNCERTAIN_FIELD from ambiguous date parsing
    for f in _DATE_RAW_FIELDS:
        raw_val = raw.get(f)
        parsed = _get(invoice, f)
        if raw_val and parsed is not None:
            try:
                pd = parse_date(str(raw_val))
                if pd.ambiguous:
                    uncertain = _get(invoice, "uncertain_fields") or []
                    if f not in uncertain:
                        issues.append(Issue(
                            code=IssueCode.UNCERTAIN_FIELD,
                            severity=Severity.warning,
                            field=f,
                            message=f"Date '{raw_val}' is ambiguous (day/month order unclear)",
                        ))
            except ValueError:
                pass

    # NEGATIVE_AMOUNT
    for f in ("subtotal", "tax", "total"):
        v = _get(invoice, f)
        if v is not None and v < Decimal("0"):
            issues.append(Issue(
                code=IssueCode.NEGATIVE_AMOUNT,
                severity=Severity.warning,
                field=f,
                got=str(v),
                message=f"Amount '{f}' is negative ({v})",
            ))
    for i, li in enumerate(line_items):
        for sub in ("amount",):
            v = _get(li, sub)
            if v is not None and v < Decimal("0"):
                issues.append(Issue(
                    code=IssueCode.NEGATIVE_AMOUNT,
                    severity=Severity.warning,
                    field=f"line_items[{i}].{sub}",
                    got=str(v),
                    message=f"Line item {i} '{sub}' is negative ({v})",
                ))
        qty = _get(li, "quantity")
        if qty is not None and qty <= Decimal("0"):
            issues.append(Issue(
                code=IssueCode.NEGATIVE_AMOUNT,
                severity=Severity.warning,
                field=f"line_items[{i}].quantity",
                got=str(qty),
                message=f"Line item {i} quantity is non-positive ({qty})",
            ))

    # DATE_ORDER: due_date < invoice_date
    inv_date = _get(invoice, "invoice_date")
    due_date = _get(invoice, "due_date")
    if inv_date and due_date and due_date < inv_date:
        issues.append(Issue(
            code=IssueCode.DATE_ORDER,
            severity=Severity.error,
            field="due_date",
            expected=f">= {inv_date}",
            got=str(due_date),
            message=f"Due date {due_date} is before invoice date {inv_date}",
        ))

    # DATE_RANGE
    today = date.today()
    for f in ("invoice_date", "due_date"):
        d = _get(invoice, f)
        if d is None:
            continue
        if d < dm:
            issues.append(Issue(
                code=IssueCode.DATE_RANGE,
                severity=Severity.warning,
                field=f,
                got=str(d),
                message=f"Date {d} is before minimum allowed {dm}",
            ))
        elif d > today + timedelta(days=dmax_days):
            issues.append(Issue(
                code=IssueCode.DATE_RANGE,
                severity=Severity.warning,
                field=f,
                got=str(d),
                message=f"Date {d} is more than {dmax_days} days in the future",
            ))

    # BAD_CURRENCY
    currency = _get(invoice, "currency")
    if currency is not None:
        canon = str(currency).upper().strip()
        if canon not in _ISO_4217:
            issues.append(Issue(
                code=IssueCode.BAD_CURRENCY,
                severity=Severity.error,
                field="currency",
                got=currency,
                message=f"Currency '{currency}' is not a valid ISO 4217 code",
            ))

    return issues


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
