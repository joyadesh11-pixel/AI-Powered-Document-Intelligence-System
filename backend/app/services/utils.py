"""Shared utility functions used by multiple service modules."""
from __future__ import annotations

from app.schemas.models import DocStatus, InvoiceStatus

_INVOICE_STATUS_RANK = {
    InvoiceStatus.needs_review.value: 1,
    InvoiceStatus.validated_ok.value: 0,
}


def compute_document_status(invoice_statuses: list[str]) -> str:
    """Return document status as worst-of its invoice statuses.

    Empty invoice list → needs_review (anomaly; let a human check).
    """
    if not invoice_statuses:
        return DocStatus.needs_review.value
    worst = max(invoice_statuses, key=lambda s: _INVOICE_STATUS_RANK.get(s, 0))
    return (
        DocStatus.needs_review.value
        if worst == InvoiceStatus.needs_review.value
        else DocStatus.validated_ok.value
    )
