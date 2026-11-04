"""Reprocess service: re-queue a document for processing.

Eligible states: failed always; needs_review only with force=True.  Anything
else (queued, processing, validated_ok) raises Conflict.
Deletes previous invoices/pages/chunks (keeps processing_events history).
Bumps attempt counter; sets status=queued.  The caller commits, then enqueues.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, DocumentPage, Invoice, ValidationIssue
from app.schemas.models import DocStatus
from app.services.errors import Conflict, NotFound


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(document_id: uuid.UUID, session: Session, *, force: bool = False) -> Document:
    """Re-queue document_id for reprocessing and return the updated Document."""
    doc = session.get(Document, document_id, with_for_update=True)
    if doc is None:
        raise NotFound(f"Document {document_id} not found")

    eligible = doc.status == DocStatus.failed.value or (
        force and doc.status == DocStatus.needs_review.value
    )
    if not eligible:
        hint = " (pass force=true)" if doc.status == DocStatus.needs_review.value else ""
        raise Conflict(f"Document status is {doc.status!r}; cannot reprocess{hint}")

    # Delete previous result data; keep processing_events for audit
    session.query(ValidationIssue).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.query(Invoice).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.query(DocumentPage).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.query(Chunk).filter_by(document_id=document_id).delete(synchronize_session=False)

    doc.attempt += 1
    doc.status = DocStatus.queued.value
    doc.error = None
    doc.error_code = None
    doc.current_node = None
    doc.finished_at = None
    doc.updated_at = _now()

    session.flush()
    return doc
