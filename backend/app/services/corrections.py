"""Corrections service: apply human patches, re-validate, recompute status.

All patch values arrive as strings; unparseable values raise CorrectionParseError
with the field name before any write (no partial write).

After applying the patch:
- Pure re-validation runs (no LLM) with corrected_fields set so grounding is
  skipped for the changed fields.
- invoice.corrected = True.
- Invoice and document statuses are recomputed (worst-of).
- The invoice's summary chunk is marked indexed=False.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Chunk, Correction, Document, DocumentPage, Invoice, LineItem, ValidationIssue
from app.pipeline.utils.dates import parse_date
from app.pipeline.utils.money import parse_amount
from app.pipeline.validation import ValidationCtx, run_all
from app.pipeline.validation.duplicates import compute_dup_key
from app.schemas.models import IssueCode, InvoiceStatus, Severity
from app.services.utils import compute_document_status


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CorrectionParseError(Exception):
    """Raised when a patch value cannot be parsed; carries the field name."""
    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


def apply(invoice_id: uuid.UUID, patch: dict, session: Session) -> Invoice:
    """Apply a human correction patch to an invoice and re-run validation.

    patch keys: any subset of {invoice_number, invoice_date, due_date,
    supplier_name, customer_name, subtotal, tax, total, currency,
    line_items: [{position, description?, quantity?, unit_price?, amount?}]}.

    All values are strings.  Raises CorrectionParseError on bad values.
    Raises ValueError if the invoice is not found.
    Returns the updated Invoice.
    """
    inv = session.get(Invoice, invoice_id)
    if inv is None:
        raise ValueError(f"Invoice {invoice_id} not found")
    doc = session.get(Document, inv.document_id)
    if doc is None:
        raise ValueError(f"Document for invoice {invoice_id} not found")

    # --- parse all values before any write ---
    parsed: dict[str, Any] = {}
    corrected_field_names: set[str] = set()

    _MONEY_FIELDS = {"subtotal", "tax", "total"}
    _DATE_FIELDS = {"invoice_date", "due_date"}
    _TEXT_FIELDS = {"invoice_number", "supplier_name", "customer_name", "currency"}

    for field_name in _TEXT_FIELDS:
        if field_name in patch:
            val = patch[field_name]
            parsed[field_name] = str(val).strip() if val not in (None, "") else None
            corrected_field_names.add(field_name)

    for field_name in _MONEY_FIELDS:
        if field_name in patch:
            raw_val = str(patch[field_name]).strip() if patch[field_name] not in (None, "") else None
            if raw_val is None:
                parsed[field_name] = None
            else:
                result = parse_amount(raw_val)
                if result is None:
                    raise CorrectionParseError(field_name, f"Cannot parse money value: {patch[field_name]!r}")
                parsed[field_name] = result
            corrected_field_names.add(field_name)

    for field_name in _DATE_FIELDS:
        if field_name in patch:
            raw_val = str(patch[field_name]).strip() if patch[field_name] not in (None, "") else None
            if raw_val is None:
                parsed[field_name] = None
            else:
                try:
                    date_result = parse_date(raw_val)
                    parsed[field_name] = str(date_result.value) if date_result.value else None
                except Exception:
                    raise CorrectionParseError(field_name, f"Cannot parse date: {patch[field_name]!r}")
            corrected_field_names.add(field_name)

    line_item_patches: list[dict] | None = None
    if "line_items" in patch:
        raw_lines = patch["line_items"]
        if not isinstance(raw_lines, list):
            raise CorrectionParseError("line_items", "Expected a list")
        line_item_patches = []
        for li_patch in raw_lines:
            position = li_patch.get("position")
            if position is None:
                raise CorrectionParseError("line_items", "Each line item patch must have a position")
            parsed_li: dict[str, Any] = {"position": int(position)}
            for li_field in ("description",):
                if li_field in li_patch:
                    parsed_li[li_field] = li_patch[li_field]
            for li_money in ("quantity", "unit_price", "amount"):
                if li_money in li_patch:
                    raw = str(li_patch[li_money]).strip() if li_patch[li_money] not in (None, "") else None
                    if raw is None:
                        parsed_li[li_money] = None
                    else:
                        result = parse_amount(raw)
                        if result is None:
                            raise CorrectionParseError(
                                f"line_items[{position}].{li_money}",
                                f"Cannot parse: {li_patch[li_money]!r}",
                            )
                        parsed_li[li_money] = result
            line_item_patches.append(parsed_li)
        corrected_field_names.add("line_items")

    # --- all values parsed; now apply writes ---
    old_values: dict[str, str | None] = {}

    for field_name, new_val in parsed.items():
        old_val = getattr(inv, field_name, None)
        old_values[field_name] = str(old_val) if old_val is not None else None
        setattr(inv, field_name, new_val)

    # Apply line-item patches by position (replace matching lines)
    if line_item_patches is not None:
        existing_lines = {li.position: li for li in inv.line_items}
        for li_patch in line_item_patches:
            pos = li_patch["position"]
            if pos in existing_lines:
                li_db = existing_lines[pos]
                for k, v in li_patch.items():
                    if k != "position":
                        if k in ("quantity", "unit_price", "amount") and v is not None:
                            setattr(li_db, k, Decimal(str(v)))
                        elif k in ("quantity", "unit_price", "amount"):
                            setattr(li_db, k, None)
                        else:
                            setattr(li_db, k, v)
            else:
                # New line item
                new_li = LineItem(
                    invoice_id=inv.id,
                    position=pos,
                )
                for k, v in li_patch.items():
                    if k != "position":
                        if k in ("quantity", "unit_price", "amount") and v is not None:
                            setattr(new_li, k, Decimal(str(v)))
                        elif k in ("quantity", "unit_price", "amount"):
                            setattr(new_li, k, None)
                        else:
                            setattr(new_li, k, v)
                session.add(new_li)
        old_values["line_items"] = None  # log as a field change

    # Log each changed field in corrections table
    for field_name in corrected_field_names:
        session.add(Correction(
            invoice_id=inv.id,
            field=field_name,
            old_value=old_values.get(field_name),
            new_value=str(parsed.get(field_name)) if parsed.get(field_name) is not None else None,
            at=_now(),
        ))

    session.flush()  # ensure line items are visible for re-validation

    # --- recompute dup_key and handle conflicts ---
    new_dup_key = compute_dup_key(inv)
    if new_dup_key != inv.dup_key:
        if new_dup_key is not None:
            existing = session.query(Invoice).filter(
                Invoice.dup_key == new_dup_key,
                Invoice.id != inv.id,
            ).first()
            if existing:
                # Correction creates a duplicate — set dup issue, dup_key=NULL, dup_of
                inv.dup_key = None
                inv.dup_of = existing.id
                # Ensure DUPLICATE_INVOICE issue is present
                has_dup = session.query(ValidationIssue).filter_by(
                    invoice_id=inv.id, code=IssueCode.DUPLICATE_INVOICE.value
                ).first()
                if not has_dup:
                    session.add(ValidationIssue(
                        document_id=inv.document_id,
                        invoice_id=inv.id,
                        code=IssueCode.DUPLICATE_INVOICE.value,
                        severity=Severity.error.value,
                        message="Corrected invoice is a duplicate of an existing invoice",
                    ))
            else:
                inv.dup_key = new_dup_key
                inv.dup_of = None
        else:
            inv.dup_key = None

    # --- re-run pure validation (no LLM) ---
    source_text = _get_source_text(doc.id, session)
    # Build a dict view for run_all (it works with dicts via _get())
    invoice_view = _invoice_to_dict(inv, session)

    ctx = ValidationCtx(
        tier=inv.tier or "parser",
        file_type=doc.file_type or "pdf",
        file_hash=doc.file_hash,
        source_text=source_text,
        seen_store=None,   # dup already handled above
        seen_in_file={},
        day_first=None,
        corrected_fields=frozenset(corrected_field_names),
    )
    new_issues = run_all(invoice_view, ctx)

    # Replace this invoice's issues
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

    # Preserve any dup issue we set above
    if inv.dup_of is not None:
        has_dup = any(
            (i.code.value if hasattr(i.code, "value") else str(i.code)) == IssueCode.DUPLICATE_INVOICE.value
            for i in new_issues
        )
        if not has_dup:
            session.add(ValidationIssue(
                document_id=inv.document_id,
                invoice_id=inv.id,
                code=IssueCode.DUPLICATE_INVOICE.value,
                severity=Severity.error.value,
                message="Invoice is a duplicate of an existing invoice",
            ))

    session.flush()

    # Recompute invoice status
    has_error = session.query(ValidationIssue).filter_by(
        invoice_id=inv.id, severity=Severity.error.value
    ).first() is not None
    inv.status = (
        InvoiceStatus.needs_review.value if has_error else InvoiceStatus.validated_ok.value
    )
    inv.corrected = True
    inv.updated_at = _now()

    # Recompute document status
    all_statuses = [
        r[0] for r in session.query(Invoice.status).filter_by(document_id=inv.document_id).all()
    ]
    doc.status = compute_document_status(all_statuses)
    doc.updated_at = _now()

    # Mark invoice summary chunk for re-embedding
    session.query(Chunk).filter_by(
        invoice_id=inv.id, kind="invoice_summary"
    ).update({"indexed": False}, synchronize_session=False)

    session.flush()
    return inv


def _get_source_text(document_id: uuid.UUID, session: Session) -> str:
    pages = (
        session.query(DocumentPage.text)
        .filter_by(document_id=document_id)
        .order_by(DocumentPage.page_no)
        .all()
    )
    return "\n".join(p.text or "" for p in pages)


def _invoice_to_dict(inv: Invoice, session: Session) -> dict:
    """Build a dict view of the invoice that run_all() can work with."""
    lines = [
        {
            "position": li.position,
            "description": li.description,
            "quantity": Decimal(str(li.quantity)) if li.quantity is not None else None,
            "unit_price": Decimal(str(li.unit_price)) if li.unit_price is not None else None,
            "amount": Decimal(str(li.amount)) if li.amount is not None else None,
        }
        for li in inv.line_items
    ]
    return {
        "unit_id": inv.unit_index,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "due_date": inv.due_date,
        "supplier_name": inv.supplier_name,
        "customer_name": inv.customer_name,
        "subtotal": Decimal(str(inv.subtotal)) if inv.subtotal is not None else None,
        "tax": Decimal(str(inv.tax)) if inv.tax is not None else None,
        "total": Decimal(str(inv.total)) if inv.total is not None else None,
        "currency": inv.currency,
        "line_items": lines,
        "uncertain_fields": inv.uncertain_fields or [],
        "raw": inv.raw or {},
        "tier": inv.tier or "parser",
        "source_pages": inv.source_pages or [],
        "issues": [],
    }
