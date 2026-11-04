"""Tests for services/reprocess.py eligibility (M8b)."""
from __future__ import annotations

import uuid

import pytest

from app.db.models import Document, ProcessingEvent
from app.services.errors import Conflict, NotFound
from app.services.reprocess import run


def _doc(session, status: str) -> Document:
    doc = Document(filename="r.pdf", file_hash=uuid.uuid4().hex * 2, file_type="pdf",
                   size_bytes=1, status=status)
    session.add(doc)
    session.commit()
    session.add(ProcessingEvent(document_id=doc.id, attempt=1, node="intake"))
    session.commit()
    return doc


def test_failed_is_requeued_and_attempt_bumped(session):
    doc = _doc(session, "failed")
    run(doc.id, session)
    session.commit()
    session.refresh(doc)
    assert (doc.status, doc.attempt) == ("queued", 2)
    assert session.query(ProcessingEvent).filter_by(document_id=doc.id).count() == 1


def test_needs_review_requires_force(session):
    doc = _doc(session, "needs_review")
    with pytest.raises(Conflict):
        run(doc.id, session)
    run(doc.id, session, force=True)
    session.commit()
    session.refresh(doc)
    assert doc.status == "queued"


@pytest.mark.parametrize("status", ["queued", "processing", "validated_ok"])
def test_other_states_conflict_even_with_force(session, status):
    doc = _doc(session, status)
    with pytest.raises(Conflict):
        run(doc.id, session, force=True)


def test_missing_document_not_found(session):
    with pytest.raises(NotFound):
        run(uuid.uuid4(), session)
