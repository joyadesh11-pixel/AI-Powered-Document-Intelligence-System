"""Read-only queries behind the GET endpoints.

Counts come from grouped subqueries (one round trip per page, no N+1).
Child collections are returned explicitly ordered (line items by position,
events by attempt then insertion) instead of relying on relationship order.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Correction, Document, DocumentPage, Invoice, LineItem, ProcessingEvent, ValidationIssue,
)
from app.schemas.models import Severity
from app.services.errors import NotFound, ServiceError
from app.services.search import escape_like

_SORTABLE = {
    "created_at": Document.created_at,
    "updated_at": Document.updated_at,
    "filename": Document.filename,
    "status": Document.status,
}


@dataclass
class DocumentRow:
    document: Document
    invoice_count: int
    issue_count: int
    error_count: int


@dataclass
class Listing:
    items: list[DocumentRow]
    total: int


@dataclass
class InvoiceDetail:
    invoice: Invoice
    line_items: list[LineItem]
    issues: list[ValidationIssue]
    corrections: list[Correction] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        # header fields (id, total, status, ...) read straight from the invoice
        return getattr(self.invoice, name)


@dataclass
class DocumentDetail:
    document: Document
    events: list[ProcessingEvent]
    invoices: list[InvoiceDetail]
    document_issues: list[ValidationIssue]


def _order_by(sort: str):
    col_name, sep, direction = sort.partition(":")
    if not sep or col_name not in _SORTABLE or direction not in ("asc", "desc"):
        raise ServiceError(
            f"sort must be one of {sorted(_SORTABLE)} with :asc or :desc", "INVALID_SORT"
        )
    col = _SORTABLE[col_name]
    return (col.asc() if direction == "asc" else col.desc()), Document.id


def list_documents(
    session: Session,
    *,
    status: str | None = None,
    q: str | None = None,
    sort: str = "created_at:desc",
    limit: int = 50,
    offset: int = 0,
) -> Listing:
    order = _order_by(sort)

    inv_counts = (
        select(Invoice.document_id, func.count().label("n"))
        .group_by(Invoice.document_id).subquery()
    )
    issue_counts = (
        select(
            ValidationIssue.document_id,
            func.count().label("n"),
            func.sum(case((ValidationIssue.severity == Severity.error.value, 1), else_=0)).label("errors"),
        )
        .group_by(ValidationIssue.document_id).subquery()
    )

    conds = []
    if status:
        conds.append(Document.status == status)
    if q:
        conds.append(Document.filename.ilike(f"%{escape_like(q)}%", escape="\\"))

    total = session.scalar(select(func.count()).select_from(Document).where(*conds)) or 0
    stmt = (
        select(
            Document,
            func.coalesce(inv_counts.c.n, 0),
            func.coalesce(issue_counts.c.n, 0),
            func.coalesce(issue_counts.c.errors, 0),
        )
        .outerjoin(inv_counts, inv_counts.c.document_id == Document.id)
        .outerjoin(issue_counts, issue_counts.c.document_id == Document.id)
        .where(*conds)
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    )
    items = [DocumentRow(doc, int(n_inv), int(n_iss), int(n_err))
             for doc, n_inv, n_iss, n_err in session.execute(stmt)]
    return Listing(items=items, total=int(total))


def _document(session: Session, document_id: uuid.UUID) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise NotFound(f"Document {document_id} not found")
    return doc


def _invoice_detail(session: Session, inv: Invoice, with_corrections: bool) -> InvoiceDetail:
    lines = list(session.scalars(
        select(LineItem).where(LineItem.invoice_id == inv.id).order_by(LineItem.position)
    ))
    issues = list(session.scalars(
        select(ValidationIssue).where(ValidationIssue.invoice_id == inv.id)
        .order_by(ValidationIssue.severity, ValidationIssue.code)
    ))
    corrections = list(session.scalars(
        select(Correction).where(Correction.invoice_id == inv.id).order_by(Correction.id)
    )) if with_corrections else []
    return InvoiceDetail(invoice=inv, line_items=lines, issues=issues, corrections=corrections)


def get_document(session: Session, document_id: uuid.UUID) -> DocumentDetail:
    doc = _document(session, document_id)
    events = list(session.scalars(
        select(ProcessingEvent).where(ProcessingEvent.document_id == doc.id)
        .order_by(ProcessingEvent.attempt, ProcessingEvent.id)
    ))
    invoices = list(session.scalars(
        select(Invoice).where(Invoice.document_id == doc.id).order_by(Invoice.unit_index, Invoice.id)
    ))
    doc_issues = list(session.scalars(
        select(ValidationIssue)
        .where(ValidationIssue.document_id == doc.id, ValidationIssue.invoice_id.is_(None))
        .order_by(ValidationIssue.severity, ValidationIssue.code)
    ))
    return DocumentDetail(
        document=doc,
        events=events,
        invoices=[_invoice_detail(session, inv, with_corrections=False) for inv in invoices],
        document_issues=doc_issues,
    )


def get_pages(session: Session, document_id: uuid.UUID) -> list[DocumentPage]:
    _document(session, document_id)
    return list(session.scalars(
        select(DocumentPage).where(DocumentPage.document_id == document_id).order_by(DocumentPage.page_no)
    ))


def get_invoice(session: Session, invoice_id: uuid.UUID) -> InvoiceDetail:
    inv = session.get(Invoice, invoice_id)
    if inv is None:
        raise NotFound(f"Invoice {invoice_id} not found")
    return _invoice_detail(session, inv, with_corrections=True)
