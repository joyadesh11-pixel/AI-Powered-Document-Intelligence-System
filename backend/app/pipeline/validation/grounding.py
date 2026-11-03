"""Grounding checks: validate extracted fields against source document text."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.pipeline.utils.text_norm import normalise
from app.schemas.models import Issue, IssueCode, Severity

try:
    from rapidfuzz import fuzz as _fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

_GROUND_FUZZY_MIN = 80


def check_grounding(invoice: Any, source_text: str, tier: str) -> list[Issue]:
    """Return NOT_GROUNDED issues; skipped for vision/sheet tiers.

    Empty OCR source text does NOT skip grounding — empty pages surface NOT_GROUNDED.
    """
    if tier in ("vision", "sheet"):
        return []

    severity = Severity.error if tier == "parser" else Severity.warning

    issues: list[Issue] = []
    norm_source = normalise(source_text)

    # Invoice number
    inv_num = _get(invoice, "invoice_number")
    if inv_num is not None:
        norm_inv = normalise(str(inv_num))
        if norm_inv and norm_inv not in norm_source:
            issues.append(Issue(
                code=IssueCode.NOT_GROUNDED,
                severity=severity,
                field="invoice_number",
                got=str(inv_num),
                message=f"invoice_number '{inv_num}' not found in source text",
            ))

    # Supplier / customer names — fuzzy match
    for f in ("supplier_name", "customer_name"):
        name = _get(invoice, f)
        if name is not None:
            if not _fuzzy_match(str(name), source_text):
                issues.append(Issue(
                    code=IssueCode.NOT_GROUNDED,
                    severity=severity,
                    field=f,
                    got=str(name),
                    message=f"{f} '{name}' not found in source text",
                ))

    # Dates: raw string must appear
    raw = _get(invoice, "raw") or {}
    for f in ("invoice_date", "due_date"):
        raw_val = raw.get(f)
        parsed = _get(invoice, f)
        if raw_val and parsed is not None:
            if str(raw_val).strip() not in source_text:
                issues.append(Issue(
                    code=IssueCode.NOT_GROUNDED,
                    severity=severity,
                    field=f,
                    got=str(raw_val),
                    message=f"{f} raw '{raw_val}' not found in source text",
                ))

    # Amounts: subtotal, tax, total, plus line amounts
    source_amounts = _extract_amounts(source_text)
    for f in ("subtotal", "tax", "total"):
        v = _get(invoice, f)
        if v is not None and source_amounts and v not in source_amounts:
            issues.append(Issue(
                code=IssueCode.NOT_GROUNDED,
                severity=severity,
                field=f,
                got=str(v),
                message=f"{f} {v} not found in source text amounts",
            ))

    line_items = _get(invoice, "line_items") or []
    for i, li in enumerate(line_items):
        amt = _get(li, "amount")
        if amt is not None and source_amounts and amt not in source_amounts:
            issues.append(Issue(
                code=IssueCode.NOT_GROUNDED,
                severity=severity,
                field=f"line_items[{i}].amount",
                got=str(amt),
                message=f"Line item {i} amount {amt} not found in source text",
            ))

    return issues


def _fuzzy_match(name: str, source_text: str, threshold: int = _GROUND_FUZZY_MIN) -> bool:
    if not name.strip():
        return True
    if _HAS_RAPIDFUZZ:
        score = _fuzz.partial_ratio(name.casefold(), source_text.casefold())
        return score >= threshold
    # Fallback: normalised substring
    return normalise(name) in normalise(source_text)


def _extract_amounts(text: str) -> set[Decimal]:
    amounts: set[Decimal] = set()
    # Match numeric tokens (no spaces inside tokens — spaces separate tokens)
    for m in re.finditer(r"-?\d[\d,.']*\d|-?\d", text):
        token = m.group().replace(",", "").replace("'", "")
        try:
            amounts.add(Decimal(token).quantize(Decimal("0.01")))
        except InvalidOperation:
            pass
    return amounts


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
