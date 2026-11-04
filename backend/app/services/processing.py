"""Document processing service.

process_document() claims the document, then drives stream_process() to get
both progress events and the final result from a single pipeline run.
Per-node events are committed in short separate transactions so the API can
read current_node while the document is still running.  The full result
(invoices, pages, issues) is committed in one final transaction.

DBSeenStore bridges the pipeline's SeenStore protocol to the invoices table.
DBMappingCache bridges the pipeline's mapping_cache callable to sheet_mapping_cache.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import DisconnectionError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.orm import Session

from app.db.models import (
    Chunk, Document, DocumentPage, Invoice, LineItem,
    ProcessingEvent, SheetMappingCache, ValidationIssue,
)
from app.pipeline.config import get_config
from app.pipeline.graph import stream_process
from app.pipeline.state import Deps
from app.pipeline.validation.duplicates import compute_dup_key
from app.schemas.models import DocStatus, ErrorCode, InvoiceStatus, IssueCode, PipelineResult, Severity
from app.services.errors import InfraError
from app.services.ingest import upload_dir
from app.services.utils import worst_status

# Connection-level DB failures: transient, so the worker may retry the task.
_INFRA_ERRORS = (OperationalError, InterfaceError, DisconnectionError)
_MAX_ERROR_CHARS = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _short_error(exc: BaseException) -> str:
    """One-line error text for the documents row: no traceback, bounded length."""
    return f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]


@dataclass
class ProcessOutcome:
    status: str
    should_index: bool
    skipped: bool = False   # True when claim() refused (redelivery or another worker owns it)


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
# Claim + main processing function
# ---------------------------------------------------------------------------

def claim(document_id: uuid.UUID, session: Session) -> bool:
    """Atomically move a document to `processing`; return False if not ours to run.

    Claimable: `queued`, or `processing` whose last update is older than the task
    time limit (the worker that owned it died).  Anything else means a duplicate
    delivery or a finished document, and the caller must not run the pipeline.
    """
    cfg = get_config()
    # updated_at is stored as naive UTC; compare against naive UTC
    now = _now().replace(tzinfo=None)
    stale_before = now - timedelta(seconds=cfg.TASK_TIME_LIMIT_S)
    result = session.execute(
        update(Document)
        .where(
            Document.id == document_id,
            or_(
                Document.status == DocStatus.queued.value,
                and_(Document.status == DocStatus.processing.value, Document.updated_at < stale_before),
            ),
        )
        .values(status=DocStatus.processing.value, error=None, error_code=None,
                current_node=None, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount == 1


def process_document(
    document_id: uuid.UUID,
    session: Session,
    llm: Any = None,
) -> ProcessOutcome:
    """Drive the pipeline for document_id, persisting progress and results.

    Per-node progress events are committed immediately so the API can show
    current_node while processing.  The full result is persisted in one final
    transaction.  DB connection failures raise InfraError (retryable) after a
    best-effort reset to `queued`; any other exception marks the document
    failed/INTERNAL_ERROR and is re-raised.
    """
    doc = session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found")

    try:
        if not claim(document_id, session):
            session.refresh(doc)
            return ProcessOutcome(status=doc.status, should_index=False, skipped=True)
        session.refresh(doc)

        cfg = get_config()
        if llm is None:
            from app.services.providers import get_llm
            llm = get_llm()

        mapping_cache = DBMappingCache(session)
        deps: Deps = {
            "config": cfg,
            "llm": llm,
            "seen_store": DBSeenStore(session),
            "mapping_cache": mapping_cache,
        }

        # Clean up data from any previous attempt (keeps processing_events history)
        _delete_previous_result_data(document_id, session)
        session.commit()

        result = _run_pipeline(doc, deps, session)
        _persist_result(document_id, result, session, mapping_cache)
        session.commit()
    except _INFRA_ERRORS as exc:
        session.rollback()
        _requeue_best_effort(document_id, session)
        raise InfraError(_short_error(exc)) from exc
    except Exception as exc:
        session.rollback()
        _mark_failed(document_id, session, exc)
        raise

    session.refresh(doc)
    return ProcessOutcome(status=doc.status, should_index=doc.status != DocStatus.failed.value)


def _run_pipeline(doc: Document, deps: Deps, session: Session) -> PipelineResult:
    """Iterate stream_process, writing one processing_events row per node."""
    attempt = doc.attempt
    result_dict: dict | None = None

    with _path_with_extension(doc) as file_path:
        result_dict = _stream_events(doc, file_path, deps, session, attempt)

    if result_dict is None:
        raise RuntimeError("stream_process completed without producing a result")
    return PipelineResult.model_validate(result_dict)


@contextmanager
def _path_with_extension(doc: Document) -> Iterator[str]:
    """Yield a path to the stored file that ends in the original extension.

    Uploads are stored as <sha256> (no extension) but the intake node decides
    sheet and image handling from the extension.  A hard link (copy if the
    filesystem refuses) in a scratch directory next to the uploads gives the
    pipeline the name it needs without changing how files are stored.
    """
    src = upload_dir() / doc.file_hash
    scratch_root = upload_dir() / "tmp"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch_root) as scratch:
        dest = Path(scratch) / f"{doc.file_hash}{Path(doc.filename).suffix.lower()}"
        try:
            os.link(src, dest)
        except OSError:
            shutil.copyfile(src, dest)
        yield str(dest)


def _stream_events(doc: Document, file_path: str, deps: Deps, session: Session,
                   attempt: int) -> dict | None:
    result_dict: dict | None = None
    for node_name, node_update in stream_process(file_path, deps=deps):
        # Capture the final result written by finalize/fail nodes
        if "result" in node_update:
            result_dict = node_update["result"]

        history_entry: dict | None = None
        for h in (node_update.get("history") or []):
            if h.get("node") == node_name:
                history_entry = h
                break

        doc.current_node = node_name
        doc.updated_at = _now()
        session.add(ProcessingEvent(
            document_id=doc.id,
            attempt=attempt,
            node=node_name,
            seconds=history_entry.get("seconds") if history_entry else None,
            ok=history_entry.get("ok") if history_entry else None,
            meta=history_entry.get("meta") if history_entry else None,
            at=_now(),
        ))
        session.commit()
    return result_dict


def _requeue_best_effort(document_id: uuid.UUID, session: Session) -> None:
    """Put the document back to `queued` so a retry can claim it (DB may still be down)."""
    try:
        session.execute(
            update(Document).where(Document.id == document_id)
            .values(status=DocStatus.queued.value, current_node=None, updated_at=_now())
            .execution_options(synchronize_session=False)
        )
        session.commit()
    except Exception:
        session.rollback()


def _mark_failed(document_id: uuid.UUID, session: Session, exc: BaseException) -> None:
    try:
        session.execute(
            update(Document).where(Document.id == document_id)
            .values(status=DocStatus.failed.value, error_code=ErrorCode.INTERNAL_ERROR.value,
                    error=_short_error(exc), finished_at=_now(), updated_at=_now())
            .execution_options(synchronize_session=False)
        )
        session.commit()
    except Exception:
        session.rollback()


def _delete_previous_result_data(document_id: uuid.UUID, session: Session) -> None:
    """Remove invoices, pages, chunks and issues from previous attempts.
    Processing events are kept for audit history.
    """
    session.query(ValidationIssue).filter_by(document_id=document_id).delete(synchronize_session=False)
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
    doc.current_node = None
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
                    # savepoint: a unique-key collision must not discard the rest of the result
                    with session.begin_nested():
                        session.add(inv_db)
                        session.flush()
                    dup_key_to_id[dup_key] = inv_db.id
                except IntegrityError:
                    # Lost a race: another document committed this key after our
                    # validate ran.  Store as a flagged duplicate, like validate would.
                    existing = session.query(Invoice).filter_by(dup_key=dup_key).first()
                    inv_db = _build_invoice_row(
                        document_id, inv_obj,
                        dup_key=None,
                        dup_of=existing.id if existing else None,
                    )
                    inv_db.status = InvoiceStatus.needs_review.value
                    session.add(inv_db)
                    session.flush()
                    session.add(ValidationIssue(
                        document_id=document_id,
                        invoice_id=inv_db.id,
                        code=IssueCode.DUPLICATE_INVOICE.value,
                        severity=Severity.error.value,
                        field="invoice_number",
                        message="Duplicate invoice found in document store (concurrent upload)",
                        location={},
                    ))
                _add_line_items_and_issues(session, inv_db, inv_obj, document_id)
                continue
            else:
                session.add(inv_db)
                session.flush()

        _add_line_items_and_issues(session, inv_db, inv_obj, document_id)

    # Document status = worst of the pipeline's verdict and the persisted invoices
    if result.status != DocStatus.failed and result.invoices:
        session.flush()
        statuses = [r[0] for r in session.query(Invoice.status).filter_by(document_id=document_id)]
        doc.status = worst_status([result.status.value, *statuses])

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

    def _date_str(v: Any) -> date | None:
        if v is None or isinstance(v, date):
            return v
        return date.fromisoformat(str(v))

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
