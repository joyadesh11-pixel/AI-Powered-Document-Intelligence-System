"""Celery tasks (run eagerly) and app configuration."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.db.models import Chunk, Document
from app.services import processing
from app.services.errors import InfraError
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.fakes import FailingEmbedder


def run(task, *args):
    """Run a task eagerly; re-raises the task's final exception like a caller would see it."""
    return task.delay(*args).get()


def _chunks(session, doc_id):
    session.expire_all()
    return session.query(Chunk).filter_by(document_id=doc_id).all()


def _status(session, doc_id):
    session.expire_all()
    return session.get(Document, doc_id).status


# ---------------------------------------------------------------- app config

def test_celery_app_settings():
    c = celery_app.conf
    assert c.task_acks_late is True
    assert c.task_reject_on_worker_lost is True
    assert c.task_time_limit == 600 and c.task_soft_time_limit == 540
    assert c.worker_prefetch_multiplier == 1
    assert c.result_backend in (None, "disabled://") or not c.result_backend
    assert c.broker_url.startswith("redis://")
    entry = c.beat_schedule["retry-unindexed"]
    assert entry["task"] == "retry_unindexed" and entry["schedule"] == 300


def test_task_names_and_retry_policy():
    assert tasks.process_document.name == "process_document"
    assert tasks.index_document.name == "index_document"
    assert tasks.retry_unindexed.name == "retry_unindexed"
    assert tasks.process_document.max_retries == 2
    assert tasks.process_document.autoretry_for == (InfraError,)


# ---------------------------------------------------------- process_document

def test_process_then_index(session, make_doc):
    doc_id = make_doc()
    run(tasks.process_document, str(doc_id))
    assert _status(session, doc_id) in ("validated_ok", "needs_review")
    chunks = _chunks(session, doc_id)
    assert chunks and all(c.indexed for c in chunks)


def test_redelivery_of_finished_document_is_a_noop(session, make_doc, llm, fake_embedder):
    doc_id = make_doc()
    run(tasks.process_document, str(doc_id))
    llm_calls, embed_calls = len(llm.calls), len(fake_embedder.calls)
    run(tasks.process_document, str(doc_id))          # acks_late redelivery
    assert len(llm.calls) == llm_calls
    assert len(fake_embedder.calls) == embed_calls


def test_infra_error_is_retried_up_to_twice(monkeypatch, make_doc):
    doc_id = make_doc()
    calls = []

    def flaky(*a, **kw):
        calls.append(1)
        raise InfraError("db blip")

    monkeypatch.setattr(processing, "process_document", flaky)
    with pytest.raises(InfraError):
        run(tasks.process_document, str(doc_id))
    assert len(calls) == 3                              # first try + max_retries=2


def test_infra_error_then_success(monkeypatch, session, make_doc):
    doc_id = make_doc()
    real = processing.process_document
    state = {"n": 0}

    def flaky(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise InfraError("db blip")
        return real(*a, **kw)

    monkeypatch.setattr(processing, "process_document", flaky)
    run(tasks.process_document, str(doc_id))
    assert state["n"] == 2
    assert _status(session, doc_id) in ("validated_ok", "needs_review")


def test_unexpected_bug_is_not_retried(monkeypatch, make_doc):
    doc_id = make_doc()
    calls = []

    def bug(*a, **kw):
        calls.append(1)
        raise ValueError("bug")

    monkeypatch.setattr(processing, "process_document", bug)
    with pytest.raises(ValueError):
        run(tasks.process_document, str(doc_id))
    assert len(calls) == 1


def test_soft_time_limit_marks_document_failed_without_retry(monkeypatch, session, make_doc):
    doc_id = make_doc()
    calls = []

    def slow(*a, **kw):
        calls.append(1)
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(processing, "_run_pipeline", slow)
    with pytest.raises(SoftTimeLimitExceeded):
        run(tasks.process_document, str(doc_id))
    assert len(calls) == 1
    session.expire_all()
    doc = session.get(Document, doc_id)
    assert doc.status == "failed" and doc.error_code == "INTERNAL_ERROR"


def test_failed_document_is_not_indexed(session, make_doc, fake_embedder, monkeypatch):
    doc_id = make_doc()
    monkeypatch.setattr(processing, "_run_pipeline",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run(tasks.process_document, str(doc_id))
    assert fake_embedder.calls == []


# ------------------------------------------------------------ index_document

def test_index_task_embedding_failure_leaves_status_and_chunks_unindexed(session, make_doc, fake_providers):
    doc_id = make_doc()
    fake_providers["embedder"] = FailingEmbedder()
    run(tasks.process_document, str(doc_id))           # processing ok, indexing quietly fails
    status = _status(session, doc_id)
    assert status in ("validated_ok", "needs_review")
    chunks = _chunks(session, doc_id)
    assert chunks and not any(c.indexed for c in chunks)


def test_index_task_survives_missing_embedder(monkeypatch, session, make_doc):
    from app.services import providers

    doc_id = make_doc()

    def no_key():
        raise RuntimeError("GOOGLE_API_KEY not set")

    monkeypatch.setattr(providers, "get_embedder", no_key)
    run(tasks.process_document, str(doc_id))
    assert _status(session, doc_id) in ("validated_ok", "needs_review")
    assert not any(c.indexed for c in _chunks(session, doc_id))


# ---------------------------------------------------------- retry_unindexed

def test_retry_unindexed_embeds_pending_chunks(session, make_doc, fake_providers):
    doc_id = make_doc()
    good = fake_providers["embedder"]
    fake_providers["embedder"] = FailingEmbedder()
    run(tasks.process_document, str(doc_id))
    assert not any(c.indexed for c in _chunks(session, doc_id))

    fake_providers["embedder"] = good                   # embedding service is back
    result = tasks.retry_unindexed.delay().get()
    assert result["embedded"] > 0
    assert all(c.indexed for c in _chunks(session, doc_id))


def test_retry_unindexed_reenqueues_only_stale_queued_documents(session, make_doc, llm):
    stale = make_doc("OLD-1", "Old Co")
    fresh = make_doc("NEW-1", "New Co")
    session.expire_all()
    session.get(Document, stale).updated_at = (
        datetime.now(timezone.utc) - timedelta(minutes=30)).replace(tzinfo=None)
    session.commit()

    result = tasks.retry_unindexed.delay().get()
    assert result["requeued"] == 1
    assert _status(session, stale) in ("validated_ok", "needs_review")
    assert _status(session, fresh) == "queued"


def test_retry_unindexed_indexes_finished_documents_with_no_chunks(session, make_doc):
    doc_id = make_doc()
    run(tasks.process_document, str(doc_id))
    session.query(Chunk).filter_by(document_id=doc_id).delete()
    session.commit()

    result = tasks.retry_unindexed.delay().get()
    assert result["indexed_docs"] == 1
    assert _chunks(session, doc_id)


def test_retry_unindexed_with_nothing_to_do(session):
    assert tasks.retry_unindexed.delay().get() == {"embedded": 0, "requeued": 0, "indexed_docs": 0}


# ------------------------------------------------------------ API dispatcher

def test_celery_dispatcher_enqueues_by_id(monkeypatch):
    from app.api.deps import CeleryDispatcher

    sent = []
    monkeypatch.setattr(tasks.process_document, "delay", lambda *a, **k: sent.append(a))
    doc_id = uuid.uuid4()
    CeleryDispatcher().process(doc_id)
    assert sent == [(str(doc_id),)]
