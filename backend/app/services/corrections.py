"""Corrections service: apply human patches, re-validate, recompute status.

All patch values arrive as strings.  Every value is parsed before any write;
if any fails, CorrectionParseError carries a {field: message} map and nothing
is written.

After applying the patch:
- Pure re-validation runs (no LLM).  Grounding is skipped for every field a
  human has ever corrected on this invoice, not just this patch's fields.
- dup_key is recomputed from the corrected values; a collision is flagged.
- invoice.corrected = True; one corrections row per field that changed.
- Invoice and document statuses are recomputed (worst-of).
- The invoice's summary chunk is rebuilt with indexed=False (re-embedded later).
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Chunk, Correction, Document, DocumentPage, Invoice, LineItem, ValidationIssue
from app.pipeline.utils.dates import parse_date
from app.pipeline.utils.money import parse_amount
from app.pipeline.validation import ValidationCtx, run_all
from app.pipeline.validation.duplicates import compute_dup_key
from app.schemas.models import DocStatus, IssueCode, InvoiceStatus, Severity
from app.services.errors import Conflict, NotFound, ServiceError
from app.services.indexing import summary_chunk
from app.services.utils import worst_status

_TEXT_FIELDS = ("invoice_number", "supplier_name", "customer_name")
_MONEY_FIELDS = ("subtotal", "tax", "total")
_DATE_FIELDS = ("invoice_date", "due_date")
_HEADER_FIELDS = (*_TEXT_FIELDS, *_MONEY_FIELDS, *_DATE_FIELDS, "currency")
_LINE_MONEY = ("quantity", "unit_price", "amount")
_LINE_FIELDS = ("description", *_LINE_MONEY)
_MAX_TEXT = 500
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")
_BUSY = (DocStatus.queued.value, DocStatus.processing.value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CorrectionParseError(ServiceError):
    """One or more patch values could not be parsed; nothing was written."""
    code = "VALIDATION_ERROR"

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors
        self.field = next(iter(errors))


# ---------------------------------------------------------------------------
# Parsing (no writes)
# ---------------------------------------------------------------------------

def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _parse_value(name: str, raw: Any, errors: dict[str, str], key: str) -> Any:
    """Parse one value; on failure record errors[key] and return None."""
    if _blank(raw):
        return None
    s = str(raw).strip()
    if name in _MONEY_FIELDS or name in _LINE_MONEY:
        value = parse_amount(s)
        if value is None:
            errors[key] = f"Cannot parse number: {s[:50]!r}"
        return value
    if name in _DATE_FIELDS:
        try:
            parsed = parse_date(s)
        except ValueError:
            errors[key] = f"Cannot parse date: {s[:50]!r}"
            return None
        if parsed.ambiguous:
            errors[key] = f"Ambiguous date {s[:50]!r}; use YYYY-MM-DD"
            return None
        return parsed.value
    if name == "currency":
        if not _CURRENCY_RE.match(s):
            errors[key] = "Currency must be a 3-letter ISO code"
            return None
        return s.upper()
    if len(s) > _MAX_TEXT:
        errors[key] = f"Longer than {_MAX_TEXT} characters"
        return None
    return s


def _parse_patch(patch: dict) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: dict[str, str] = {}
    header: dict[str, Any] = {}
    lines: list[dict[str, Any]] = []

    for key, raw in patch.items():
        if key == "line_items":
            continue
        if key not in _HEADER_FIELDS:
            errors[key] = "Unknown field"
            continue
        header[key] = _parse_value(key, raw, errors, key)

    raw_lines = patch.get("line_items")
    if raw_lines is not None:
        if not isinstance(raw_lines, list):
            errors["line_items"] = "Expected a list"
        else:
            seen: set[int] = set()
            for i, li in enumerate(raw_lines):
                pos = li.get("position") if isinstance(li, dict) else None
                try:
                    pos = int(pos)
                except (TypeError, ValueError):
                    errors[f"line_items[{i}].position"] = "Each line item needs an integer position"
                    continue
                if pos < 1 or pos in seen:
                    errors[f"line_items[{i}].position"] = "Position must be >= 1 and unique"
                    continue
                seen.add(pos)
                parsed: dict[str, Any] = {"position": pos}
                for k, raw in li.items():
                    if k == "position":
                        continue
                    if k not in _LINE_FIELDS:
                        errors[f"line_items[{pos}].{k}"] = "Unknown field"
                        continue
                    parsed[k] = _parse_value(k, raw, errors, f"line_items[{pos}].{k}")
                lines.append(parsed)

    if errors:
        raise CorrectionParseError(errors)
    return header, lines


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _as_date(v: Any) -> date | None:
    """Columns can hold an ISO string until the session reloads; validation needs a date."""
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm(v: Any) -> Any:
    """Comparable form: stored ISO-date strings become dates; Decimals already compare by value."""
    if isinstance(v, str) and _ISO_DATE_RE.match(v):
        return date.fromisoformat(v)
    return v


def _text(v: Any) -> str | None:
    return None if v is None else str(v)


def apply(invoice_id: uuid.UUID, patch: dict, session: Session) -> Invoice:
    """Apply a human correction patch to an invoice and re-run validation.

    patch keys: any subset of {invoice_number, invoice_date, due_date,
    supplier_name, customer_name, subtotal, tax, total, currency,
    line_items: [{position, description?, quantity?, unit_price?, amount?}]}.
    Values are strings; "" or None clears a field.

    Raises CorrectionParseError (nothing written), NotFound, or Conflict while
    the document is queued/processing.  The caller commits.
    """
    inv = session.get(Invoice, invoice_id, with_for_update=True)
    if inv is None:
        raise NotFound(f"Invoice {invoice_id} not found")
    doc = session.get(Document, inv.document_id)
    if doc.status in _BUSY:
        raise Conflict(f"Document is {doc.status}; corrections are allowed once processing finishes")

    header, line_patches = _parse_patch(patch)

    # --- writes: only fields whose value actually changes are logged
    changes: list[tuple[str, Any, Any]] = []
    for name, new in header.items():
        old = getattr(inv, name)
        if _norm(old) != _norm(new):
            changes.append((name, old, new))
            setattr(inv, name, new)

    lines_by_pos = {li.position: li for li in inv.line_items}
    for lp in line_patches:
        pos = lp["position"]
        li = lines_by_pos.get(pos)
        if li is None:
            li = LineItem(invoice_id=inv.id, position=pos)
            inv.line_items.append(li)
        for k, new in lp.items():
            if k == "position":
                continue
            old = getattr(li, k)
            if _norm(old) != _norm(new):
                changes.append((f"line_items[{pos}].{k}", old, new))
                setattr(li, k, new)

    for field_name, old, new in changes:
        session.add(Correction(invoice_id=inv.id, field=field_name,
                               old_value=_text(old), new_value=_text(new), at=_now()))
    session.flush()

    # every field a human has ever corrected on this invoice skips grounding
    ever = {r[0] for r in session.query(Correction.field).filter_by(invoice_id=inv.id)}
    corrected_fields = frozenset(
        "line_items" if f.startswith("line_items[") else f for f in ever
    )

    _recompute_dup_key(inv, session)
    _revalidate(inv, doc, corrected_fields, session)

    if changes:
        inv.corrected = True
    inv.updated_at = _now()
    session.flush()

    _recompute_document_status(doc, session)
    _rebuild_summary_chunk(inv, session)
    session.flush()
    return inv


def _recompute_dup_key(inv: Invoice, session: Session) -> None:
    new_key = compute_dup_key(inv)
    if new_key is None:
        inv.dup_key, inv.dup_of = None, None
        return
    holder = session.query(Invoice).filter(Invoice.dup_key == new_key, Invoice.id != inv.id).first()
    if holder is not None:
        inv.dup_key, inv.dup_of = None, holder.id
        return
    inv.dup_key, inv.dup_of = new_key, None
    try:
        # savepoint: a concurrent write may take the key between check and flush
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        holder = session.query(Invoice).filter(Invoice.dup_key == new_key, Invoice.id != inv.id).first()
        inv.dup_key, inv.dup_of = None, holder.id if holder else None


def _revalidate(inv: Invoice, doc: Document, corrected_fields: frozenset[str], session: Session) -> None:
    """Replace this invoice's issues with a fresh pure-validation run."""
    ctx = ValidationCtx(
        tier=inv.tier or "parser",
        file_type=doc.file_type or "pdf",
        file_hash=doc.file_hash,
        source_text=_get_source_text(doc.id, session),
        seen_store=None,   # duplicates handled by _recompute_dup_key
        seen_in_file={},
        day_first=None,
        corrected_fields=corrected_fields,
    )
    view = _invoice_to_dict(inv)
    # a human-confirmed value is no longer "uncertain"
    view["uncertain_fields"] = [f for f in view["uncertain_fields"] if f not in corrected_fields]
    new_issues = run_all(view, ctx)

    session.query(ValidationIssue).filter_by(invoice_id=inv.id).delete(synchronize_session=False)
    for issue in new_issues:
        session.add(ValidationIssue(
            document_id=inv.document_id,
            invoice_id=inv.id,
            code=issue.code.value if hasattr(issue.code, "value") else str(issue.code),
            severity=issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity),
            field=issue.field,
            expected=issue.expected,
            got=issue.got,
            message=issue.message,
            location=issue.location or {},
        ))
    has_error = any(
        (i.severity.value if hasattr(i.severity, "value") else str(i.severity)) == Severity.error.value
        for i in new_issues
    )
    if inv.dup_of is not None:
        session.add(ValidationIssue(
            document_id=inv.document_id,
            invoice_id=inv.id,
            code=IssueCode.DUPLICATE_INVOICE.value,
            severity=Severity.error.value,
            field="invoice_number",
            message="Invoice is a duplicate of an existing invoice",
            location={},
        ))
        has_error = True
    inv.status = InvoiceStatus.needs_review.value if has_error else InvoiceStatus.validated_ok.value


def _recompute_document_status(doc: Document, session: Session) -> None:
    """Worst of its invoices (docs/06).  Document-level pipeline notes such as
    VISION_FAILED describe the run, not the data, so they do not block it."""
    statuses = [r[0] for r in session.query(Invoice.status).filter_by(document_id=doc.id)]
    doc.status = worst_status(statuses) if statuses else DocStatus.needs_review.value
    doc.updated_at = _now()


def _rebuild_summary_chunk(inv: Invoice, session: Session) -> None:
    session.query(Chunk).filter_by(invoice_id=inv.id, kind="invoice_summary").delete(
        synchronize_session=False
    )
    chunk = summary_chunk(inv)
    if chunk is not None:
        session.add(chunk)


def _get_source_text(document_id: uuid.UUID, session: Session) -> str:
    pages = (
        session.query(DocumentPage.text)
        .filter_by(document_id=document_id)
        .order_by(DocumentPage.page_no)
        .all()
    )
    return "\n".join(p.text or "" for p in pages)


def _dec(v: Any) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def _invoice_to_dict(inv: Invoice) -> dict:
    """Build a dict view of the invoice that run_all() can work with."""
    lines = [
        {
            "position": li.position,
            "description": li.description,
            "quantity": _dec(li.quantity),
            "unit_price": _dec(li.unit_price),
            "amount": _dec(li.amount),
        }
        for li in sorted(inv.line_items, key=lambda li: li.position)
    ]
    return {
        "unit_id": inv.unit_index,
        "invoice_number": inv.invoice_number,
        "invoice_date": _as_date(inv.invoice_date),
        "due_date": _as_date(inv.due_date),
        "supplier_name": inv.supplier_name,
        "customer_name": inv.customer_name,
        "subtotal": _dec(inv.subtotal),
        "tax": _dec(inv.tax),
        "total": _dec(inv.total),
        "currency": inv.currency,
        "line_items": lines,
        "uncertain_fields": inv.uncertain_fields or [],
        "raw": inv.raw or {},
        "tier": inv.tier or "parser",
        "source_pages": inv.source_pages or [],
        "issues": [],
    }
