"""Tests for services/indexing.py batching and retry (M8b)."""
from __future__ import annotations

from app.db.models import Chunk, Document, DocumentPage
from app.services.indexing import embed_pending, index_document
from tests.fakes import FailingEmbedder, FakeEmbedder


def _doc_with_pages(session, n_pages: int, status: str = "needs_review") -> Document:
    doc = Document(filename="p.pdf", file_hash=f"{n_pages:064d}", file_type="pdf",
                   size_bytes=1, status=status)
    session.add(doc)
    session.flush()
    for i in range(n_pages):
        session.add(DocumentPage(document_id=doc.id, page_no=i + 1, text=f"page {i} text", source="digital"))
    session.commit()
    return doc


def test_embeds_in_batches_of_32(session):
    doc = _doc_with_pages(session, 70)
    emb = FakeEmbedder()
    index_document(doc.id, session, emb)
    session.commit()

    assert [c["n"] for c in emb.calls] == [32, 32, 6]
    assert all(c["task_type"] == "RETRIEVAL_DOCUMENT" for c in emb.calls)
    assert session.query(Chunk).filter_by(document_id=doc.id, indexed=True).count() == 70


def test_long_page_chunked_with_overlap(session):
    doc = Document(filename="l.pdf", file_hash="9" * 64, file_type="pdf", size_bytes=1, status="validated_ok")
    session.add(doc)
    session.flush()
    text = "".join(f"w{i:04d}" for i in range(500))  # 2500 chars, every substring unique
    session.add(DocumentPage(document_id=doc.id, page_no=1, text=text, source="digital"))
    session.commit()

    index_document(doc.id, session, FakeEmbedder())
    session.commit()
    parts = [c.text for c in session.query(Chunk).filter_by(document_id=doc.id).order_by(Chunk.text)]
    chunks = sorted(parts, key=lambda t: text.index(t))
    assert [len(c) for c in chunks] == [1000, 1000, 700]
    assert chunks[0][-100:] == chunks[1][:100]


def test_failed_batch_leaves_only_its_chunks_unindexed_and_status_untouched(session):
    doc = _doc_with_pages(session, 70, status="needs_review")
    index_document(doc.id, session, FailingEmbedder(fail_on={1}))
    session.commit()

    assert session.query(Chunk).filter_by(document_id=doc.id, indexed=False).count() == 32
    assert session.query(Chunk).filter_by(document_id=doc.id, indexed=True).count() == 38
    session.refresh(doc)
    assert doc.status == "needs_review"


def test_total_embed_failure_does_not_raise(session):
    doc = _doc_with_pages(session, 3, status="validated_ok")
    index_document(doc.id, session, FailingEmbedder())
    session.commit()
    session.refresh(doc)
    assert doc.status == "validated_ok"
    assert session.query(Chunk).filter_by(document_id=doc.id, indexed=False).count() == 3


def test_embed_pending_only_touches_unindexed(session):
    doc = _doc_with_pages(session, 70)
    index_document(doc.id, session, FailingEmbedder(fail_on={1}))
    session.commit()

    emb = FakeEmbedder()
    n = embed_pending(session, emb)
    session.commit()

    assert n == 32
    assert [c["n"] for c in emb.calls] == [32]
    assert session.query(Chunk).filter_by(indexed=False).count() == 0


class _WrongDimEmbedder(FakeEmbedder):
    """Returns vectors of the wrong size, like a model ignoring output_dimensionality."""

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
        self.calls.append({"n": len(texts), "task_type": task_type})
        return [[0.1] * 3072 for _ in texts]


def test_wrong_dimension_vectors_leave_chunks_unindexed_without_crashing(session):
    doc = _doc_with_pages(session, 3)
    done = index_document(doc.id, session, _WrongDimEmbedder())
    session.commit()

    assert done == 0
    chunks = session.query(Chunk).filter_by(document_id=doc.id).all()
    assert len(chunks) == 3 and not any(c.indexed for c in chunks)
    assert all(c.embedding is None for c in chunks)
