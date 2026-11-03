"""Pure validation layer for invoice extraction pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.models import Issue, IssueCode, Severity

from .arithmetic import check_arithmetic
from .completeness import check_completeness
from .duplicates import InMemorySeenStore, SeenStore, check_duplicate
from .formats import check_formats
from .grounding import check_grounding

__all__ = [
    "ESCALATION_CODES",
    "ValidationCtx",
    "InMemorySeenStore",
    "SeenStore",
    "run_all",
]

ESCALATION_CODES: frozenset[IssueCode] = frozenset({
    IssueCode.MISSING_REQUIRED,
    IssueCode.BAD_NUMBER,
    IssueCode.BAD_DATE,
    IssueCode.BAD_CURRENCY,
    IssueCode.ARITH_LINE,
    IssueCode.ARITH_SUBTOTAL,
    IssueCode.ARITH_TOTAL,
    IssueCode.NOT_GROUNDED,
    IssueCode.NO_INVOICE_DETECTED,
})


@dataclass
class ValidationCtx:
    tier: str
    file_type: str
    file_hash: str
    source_text: str
    seen_store: Any  # SeenStore | None
    seen_in_file: dict = field(default_factory=dict)
    day_first: bool | None = None
    escalated: bool = False
    tol_abs: Any = None  # reserved for future use
    date_min: Any = None
    date_max_days_ahead: int | None = None
    ground_fuzzy_min: int = 80
    # Fields a human corrected via PATCH; grounding check is skipped for these
    corrected_fields: frozenset[str] = field(default_factory=frozenset)


def run_all(invoice: Any, ctx: ValidationCtx) -> list[Issue]:
    """Run all validation rules and return de-duplicated issues."""
    issues: list[Issue] = []

    issues.extend(check_completeness(invoice))
    issues.extend(check_formats(
        invoice,
        date_min=ctx.date_min,
        date_max_days_ahead=ctx.date_max_days_ahead,
    ))
    issues.extend(check_arithmetic(invoice))
    issues.extend(check_grounding(invoice, ctx.source_text, ctx.tier, corrected_fields=ctx.corrected_fields))

    dup = check_duplicate(invoice, ctx.seen_in_file, ctx.seen_store, ctx.file_hash)
    if dup is not None:
        issues.append(dup)

    # De-duplicate by (code, field)
    seen: set[tuple] = set()
    unique: list[Issue] = []
    for iss in issues:
        key = (iss.code, iss.field)
        if key not in seen:
            seen.add(key)
            unique.append(iss)

    return unique
