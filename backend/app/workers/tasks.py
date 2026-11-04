"""Celery tasks: thin wrappers over the services.

process_document   claim + run the pipeline + persist; then enqueue indexing.
index_document     chunk + embed one document. Embedding trouble never fails it.
retry_unindexed    periodic safety net (beat, every 5 min).

Only transient infrastructure errors (InfraError) are retried (max 2).
Pipeline outcomes and bugs are recorded on the document, not retried.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select
from sqlalchemy.exc import InterfaceError, OperationalError

from app.db.models import Chunk, Document, DocumentPage, Invoice
from app.pipeline.config import get_config
from app.schemas.models import DocStatus
from app.services import indexing, processing, providers
from app.services.errors import InfraError
from app.services.session import new_session
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)

_RETRY = dict(
    autoretry_for=(InfraError,),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
_BATCH = 100        # documents handled per retry_unindexed run


@contextmanager
def _session():
    """A session whose connection failures surface as retryable InfraError."""
    try:
        with new_session() as s:
            yield s
    except (OperationalError, InterfaceError) as exc:
        raise InfraError(f"{type(exc).__name__}: database unavailable") from exc


def _embedder():
    """The embedder, or None if it cannot be built (chunks then stay unindexed)."""
    try:
        return providers.get_embedder()
    except Exception as exc:
        log.warning("Embedder unavailable: %s", type(exc).__name__)
        return None


@celery_app.task(name="process_document", **_RETRY)
def process_document(document_id: str) -> str:
    doc_uuid = uuid.UUID(document_id)
    with _session() as s:
        outcome = processing.process_document(doc_uuid, s)
    if outcome.skipped:
        log.info("process_document %s skipped (not claimable)", document_id)
    elif outcome.should_index:
        index_document.delay(document_id)
    return outcome.status


@celery_app.task(name="index_document", **_RETRY)
def index_document(document_id: str) -> int:
    embedder = _embedder()
    if embedder is None:
        return 0
    with _session() as s:
        n = indexing.index_document(uuid.UUID(document_id), s, embedder)
        s.commit()
    return n


@celery_app.task(name="retry_unindexed")
def retry_unindexed() -> dict[str, int]:
    """Embed leftover chunks, re-enqueue stuck `queued` documents, index documents
    that finished without chunks. Every step is idempotent."""
    cfg = get_config()
    embedded = requeued = indexed_docs = 0

    embedder = _embedder()
    with _session() as s:
        if embedder is not None:
            embedded = indexing.embed_pending(s, embedder)
            s.commit()

        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cfg.STALE_QUEUED_MINUTES)).replace(tzinfo=None)
        stale = list(s.scalars(
            select(Document.id)
            .where(Document.status == DocStatus.queued.value, Document.updated_at < cutoff)
            .limit(_BATCH)
        ))
        unchunked = list(s.scalars(
            select(Document.id)
            .where(
                Document.status.in_([DocStatus.validated_ok.value, DocStatus.needs_review.value]),
                ~exists().where(Chunk.document_id == Document.id),
                exists().where(DocumentPage.document_id == Document.id)
                | exists().where(Invoice.document_id == Document.id),
            )
            .limit(_BATCH)
        ))

    for doc_id in stale:
        process_document.delay(str(doc_id))
        requeued += 1
    for doc_id in unchunked:
        index_document.delay(str(doc_id))
        indexed_docs += 1
    return {"embedded": embedded, "requeued": requeued, "indexed_docs": indexed_docs}
