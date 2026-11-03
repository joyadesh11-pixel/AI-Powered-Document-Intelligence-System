"""Document processing service.

process_document() drives stream_process() to get both progress events and the
final result from a single pipeline run.  Per-node events are committed in short
separate transactions so the API can read current_node while the document is
still running.  The full result (invoices, pages, issues) is committed in one
final transaction.

DBSeenStore bridges the pipeline's SeenStore protocol to the invoices table.
DBMappingCache bridges the pipeline's mapping_cache callable to sheet_mapping_cache.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Chunk, Document, DocumentPage, Invoice, LineItem,
    ProcessingEvent, SheetMappingCache, ValidationIssue,
)
from app.pipeline.config import get_config
from app.pipeline.graph import stream_process
from app.pipeline.state import Deps
from app.pipeline.validation.duplicates import compute_dup_key
from app.schemas.models import DocStatus, ErrorCode, IssueCode, PipelineResult

_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# SeenStore backed by the DB
# ---------------------------------------------------------------------------

class DBSeenStore:
    """Checks the invoices table for existing dup_keys.

    exclude_hash: skip invoices belonging to documents with this file_hash so
    reprocessing the same file does not flag its own invoices as duplicates.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def contains(self, key: str, *, exclude_hash: str | None = None) -> bool:
        q = (
            self._session.query(Invoice.id)
            .filter(Invoice.dup_key == key)
        )
        if exclude_hash:
            q = q.join(Document, Invoice.document_id == Document.id).filter(
                Document.file_hash != exclude_hash
            )
        return q.limit(1).count() > 0


# ---------------------------------------------------------------------------
# MappingCache backed by sheet_mapping_cache table
# ---------------------------------------------------------------------------

class DBMappingCache:
    def __init__(self, session: Session) -> None:
        self._session = session

    def __call__(self, header_hash: str) -> Any:
        """Return a SheetMapping-compatible dict or None."""
        row = self._session.get(SheetMappingCache, header_hash)
        if row is None:
            return None
        row.last_used_at = _now()
        return row.mapping_json  # dict; pipeline rebuilds SheetMapping from it

    def store(self, header_hash: str, mapping_dict: dict) -> None:
        existing = self._session.get(SheetMappingCache, header_hash)
        if existing:
            existing.mapping_json = mapping_dict
            existing.last_used_at = _now()
        else:
            self._session.add(SheetMappingCache(
                header_hash=header_hash,
                mapping_json=mapping_dict,
            ))


# ---------------------------------------------------------------------------
# Main processing function
# ---------------------------------------------------------------------------

def process_document(
    document_id: uuid.UUID,
    session: Session,
    llm: Any = None,
    embedder: Any = None,
) -> None:
    """Drive the pipeline for document_id, persisting progress and results.

    Per-node progress events are committed immediately so the API can show
    current_node while processing.  The full result is persisted in one final
    transaction.  On any unexpected exception: status=failed, INTERNAL_ERROR.
    """
    doc = session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found")

    cfg = get_config()
    if llm is None:
        from app.pipeline.llm.client import build_llm_client
        llm = build_llm_client(cfg)

    mapping_cache = DBMappingCache(session)
    seen_store = DBSeenStore(session)

    deps: Deps = {
        "config": cfg,
        "llm": llm,
        "seen_store": seen_store,
        "mapping_cache": mapping_cache,
    }

    # Clean up data from any previous attempt (keeps processing_events history)
    _delete_previous_result_data(document_id, session)

    doc.status = "processing"
    doc.error = None
    doc.error_code = None
    doc.current_node = None
    doc.updated_at = _now()
    session.commit()

    file_path = str(_UPLOAD_DIR / doc.file_hash)
    attempt = doc.attempt

    try:
        result_dict: dict | None = None

        for node_name, update in stream_process(file_path, deps=deps):
            # Capture the final result written by finalize/fail nodes
            if "result" in update:
                result_dict = update["result"]

            # Short commit: update current_node + write event
            try:
                doc_reload = session.get(Document, document_id)
                if doc_reload is not None:
                    doc_reload.current_node = node_name
                    doc_reload.updated_at = _now()

                history_entry: dict | None = None
                for h in (update.get("history") or []):
                    if h.get("node") == node_name:
                        history_entry = h
                        break

                session.add(ProcessingEvent(
                    document_id=document_id,
                    attempt=attempt,
                    node=node_name,
                    seconds=history_entry.get("seconds") if history_entry else None,
                    ok=history_entry.get("ok") if history_entry else None,
                    meta=history_entry.get("meta") if history_entry else None,
                    at=_now(),
                ))
                session.commit()
            except Exception:
                session.rollback()

        if result_dict is None:
            raise RuntimeError("stream_process completed without producing a result")

        result = PipelineResult.model_validate(result_dict)

    except Exception as exc:
        try:
            doc_reload = session.get(Document, document_id)
            if doc_reload is not None:
                doc_reload.status = DocStatus.failed.value
                doc_reload.error_code = ErrorCode.INTERNAL_ERROR.value
                doc_reload.error = str(exc)
                doc_reload.finished_at = _now()
                doc_reload.updated_at = _now()
                session.commit()
        except Exception:
            session.rollback()
        raise

    try:
        _persist_result(document_id, result, session, mapping_cache)
        session.commit()
    except Exception:
        session.rollback()
        try:
            doc_reload = session.get(Document, document_id)
            if doc_reload is not None:
                doc_reload.status = DocStatus.failed.value
                doc_reload.error_code = ErrorCode.INTERNAL_ERROR.value
                doc_reload.error = "Failed to persist pipeline result"
                doc_reload.finished_at = _now()
                doc_reload.updated_at = _now()
                session.commit()
        except Exception:
            session.rollback()
        raise


def _delete_previous_result_data(document_id: uuid.UUID, session: Session) -> None:
    """Remove invoices, pages, and chunks from previous attempts.
    Processing events are kept for audit history.
    """
    session.query(Invoice).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.query(DocumentPage).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.query(Chunk).filter_by(document_id=document_id).delete(synchronize_session=False)


def _persist_result(
    document_id: uuid.UUID,
    result: PipelineResult,
    session: Session,
    mapping_cache: DBMappingCache,
) -> None:
    """Write PipelineResult to the DB inside the caller's transaction."""
    doc = session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found during persist")

    doc.status = result.status.value
    doc.error_code = result.error_code.value if result.error_code else None
    doc.error = result.error
    doc.file_type = result.file_type or doc.file_type
    doc.size_bytes = result.size_bytes or doc.size_bytes
    doc.page_count = result.page_count
    doc.pipeline_version = result.pipeline_version
    doc.timings = result.timings
    doc.llm_usage = result.llm_usage
    doc.finished_at = _now()
    doc.updated_at = _now()

    tier_counts: dict[str, int] = {}
    for inv in result.invoices:
        t = inv.tier.value if hasattr(inv.tier, "value") else str(inv.tier)
        tier_counts[t] = tier_counts.get(t, 0) + 1
    doc.tier_summary = tier_counts

    # Pages
    for page_no, text in result.page_text.items():
        session.add(DocumentPage(
            document_id=document_id,
            page_no=int(page_no),
            text=text,
            source=result.page_source.get(page_no),
            ocr_conf=result.ocr_conf.get(page_no),
        ))

    # Document-level warnings (no invoice_id)
    for warn in result.warnings:
        session.add(ValidationIssue(
            document_id=document_id,
            invoice_id=None,
            code=warn.code.value if hasattr(warn.code, "value") else str(warn.code),
            severity=warn.severity.value if hasattr(warn.severity, "value") else str(warn.severity),
            field=warn.field,
            expected=warn.expected,
            got=warn.got,
            message=warn.message,
            location=warn.location or {},
        ))

    # Invoices — handle dup_key uniqueness and dup_of linkage
    dup_key_to_id: dict[str, uuid.UUID] = {}

    for inv_obj in result.invoices:
        dup_key = compute_dup_key(inv_obj)

        has_dup_issue = any(
            (i.code.value if hasattr(i.code, "value") else str(i.code)) == IssueCode.DUPLICATE_INVOICE.value
            for i in inv_obj.issues
        )

        if has_dup_issue and dup_key is not None:
            existing = session.query(Invoice).filter_by(dup_key=dup_key).first()
            existing_id = existing.id if existing else dup_key_to_id.get(dup_key)
            inv_db = _build_invoice_row(document_id, inv_obj, dup_key=None, dup_of=existing_id)
            session.add(inv_db)
            session.flush()
        else:
            inv_db = _build_invoice_row(document_id, inv_obj, dup_key=dup_key, dup_of=None)
            if dup_key is not None:
                try:
                    session.add(inv_db)
                    session.flush()
                    dup_key_to_id[dup_key] = inv_db.id
                except IntegrityError:
                    session.rollback()
                    existing = session.query(Invoice).filter_by(dup_key=dup_key).first()
                    inv_db = _build_invoice_row(
                        document_id, inv_obj,
                        dup_key=None,
                        dup_of=existing.id if existing else None,
                    )
                    session.add(inv_db)
                    session.flush()
                _add_line_items_and_issues(session, inv_db, inv_obj, document_id)
                continue
            else:
                session.add(inv_db)
                session.flush()

        _add_line_items_and_issues(session, inv_db, inv_obj, document_id)

    # Store sheet mapping in cache
    if result.header_hash and result.sheet_mapping:
        mapping_dict = (
            result.sheet_mapping.model_dump()
            if hasattr(result.sheet_mapping, "model_dump")
            else dict(result.sheet_mapping)
        )
        mapping_cache.store(result.header_hash, mapping_dict)


def _build_invoice_row(
    document_id: uuid.UUID,
    inv_obj: Any,
    *,
    dup_key: str | None,
    dup_of: uuid.UUID | None,
) -> Invoice:
    def _dec(v: Any) -> Any:
        return None if v is None else Decimal(str(v))

    def _date_str(v: Any) -> str | None:
        return None if v is None else str(v)

    return Invoice(
        document_id=document_id,
        unit_index=inv_obj.unit_id,
        invoice_number=inv_obj.invoice_number,
        invoice_date=_date_str(inv_obj.invoice_date),
        due_date=_date_str(inv_obj.due_date),
        supplier_name=inv_obj.supplier_name,
        customer_name=inv_obj.customer_name,
        subtotal=_dec(inv_obj.subtotal),
        tax=_dec(inv_obj.tax),
        total=_dec(inv_obj.total),
        currency=inv_obj.currency,
        status=inv_obj.status.value if hasattr(inv_obj.status, "value") else str(inv_obj.status),
        corrected=bool(getattr(inv_obj, "corrected", False)),
        tier=inv_obj.tier.value if hasattr(inv_obj.tier, "value") else str(inv_obj.tier),
        uncertain_fields=list(inv_obj.uncertain_fields) if inv_obj.uncertain_fields else [],
        raw=dict(inv_obj.raw) if inv_obj.raw else {},
        extra=dict(inv_obj.extra) if inv_obj.extra else {},
        derived=dict(inv_obj.derived) if inv_obj.derived else {},
        source_pages=list(inv_obj.source_pages) if inv_obj.source_pages else [],
        source_rows=list(inv_obj.source_rows) if inv_obj.source_rows else [],
        alternatives=list(inv_obj.alternatives) if inv_obj.alternatives else [],
        dup_key=dup_key,
        dup_of=dup_of,
    )


def _add_line_items_and_issues(
    session: Session,
    inv_db: Invoice,
    inv_obj: Any,
    document_id: uuid.UUID,
) -> None:
    def _dec(v: Any) -> Any:
        return None if v is None else Decimal(str(v))

    for li in inv_obj.line_items:
        session.add(LineItem(
            invoice_id=inv_db.id,
            position=li.position,
            description=li.description,
            quantity=_dec(li.quantity),
            unit_price=_dec(li.unit_price),
            amount=_dec(li.amount),
        ))

    for issue in inv_obj.issues:
        session.add(ValidationIssue(
            document_id=document_id,
            invoice_id=inv_db.id,
            code=issue.code.value if hasattr(issue.code, "value") else str(issue.code),
            severity=issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity),
            field=issue.field,
            expected=issue.expected,
            got=issue.got,
            message=issue.message,
            location=issue.location or {},
        ))
