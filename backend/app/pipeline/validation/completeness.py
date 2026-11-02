"""Completeness checks: missing required/optional fields, empty lines, no invoice."""
from __future__ import annotations

from typing import Any

from app.schemas.models import Issue, IssueCode, Severity


_REQUIRED_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "total")
_OPTIONAL_FIELDS = ("customer_name", "currency", "subtotal", "tax")


def check_completeness(invoice: Any) -> list[Issue]:
    issues: list[Issue] = []

    missing_required: list[str] = []
    for f in _REQUIRED_FIELDS:
        if _get(invoice, f) is None:
            missing_required.append(f)
            issues.append(Issue(
                code=IssueCode.MISSING_REQUIRED,
                severity=Severity.error,
                field=f,
                message=f"Required field '{f}' is missing",
            ))

    for f in _OPTIONAL_FIELDS:
        if _get(invoice, f) is None:
            issues.append(Issue(
                code=IssueCode.MISSING_OPTIONAL,
                severity=Severity.info,
                field=f,
                message=f"Optional field '{f}' is absent",
            ))

    line_items = _get(invoice, "line_items") or []
    if not line_items:
        issues.append(Issue(
            code=IssueCode.LINES_MISSING,
            severity=Severity.warning,
            field="line_items",
            message="No line items found",
        ))

    # NO_INVOICE_DETECTED: all four required fields null AND no lines
    if len(missing_required) == len(_REQUIRED_FIELDS) and not line_items:
        issues.append(Issue(
            code=IssueCode.NO_INVOICE_DETECTED,
            severity=Severity.error,
            field=None,
            message="No invoice detected: all header fields are null and there are no line items",
        ))

    # UNCERTAIN_FIELD from invoice.uncertain_fields
    uncertain = _get(invoice, "uncertain_fields") or []
    for f in uncertain:
        issues.append(Issue(
            code=IssueCode.UNCERTAIN_FIELD,
            severity=Severity.warning,
            field=f,
            message=f"Field '{f}' was flagged as uncertain by the extractor",
        ))

    return issues


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
