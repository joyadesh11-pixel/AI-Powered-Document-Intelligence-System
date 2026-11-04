"""Worker test fixtures: eager Celery (no broker), real Postgres, fake LLM/embedder."""
from __future__ import annotations

import pytest

from app.services.ingest import save_upload
from app.workers.celery_app import celery_app
from tests.api.helpers import fake_invoice, make_pdf
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def _clean(truncate_tables, upload_dir, session_factory, fake_providers):
    yield


@pytest.fixture(autouse=True)
def eager():
    previous = (celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False   # needed for autoretry to run eagerly; .get() re-raises
    yield
    celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates = previous


@pytest.fixture
def make_doc(session, fake_providers):
    """Store a PDF as a queued document and queue one matching LLM answer."""
    def _make(number="INV-001", supplier="Acme Corp", total="100.00"):
        fake_providers["llm"].responses.append(fake_invoice(number, supplier, total))
        res = save_upload(f"{number}.pdf", make_pdf(number, supplier, total), session)
        session.commit()
        return res.document.id
    return _make


@pytest.fixture
def llm(fake_providers) -> FakeLLM:
    return fake_providers["llm"]
