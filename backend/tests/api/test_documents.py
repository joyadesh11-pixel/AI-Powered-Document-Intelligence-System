"""POST/GET /api/documents, pages, file, reprocess."""
from __future__ import annotations

import pytest

from app.db.models import Document
from tests.api.helpers import fake_invoice, make_pdf, pdf_file
from tests.fakes import FakeLLM


def _upload(client, *files):
    return client.post("/api/documents", files=list(files))


def _ready(fake_providers, *invoices):
    fake_providers["llm"] = FakeLLM(responses=list(invoices))


def test_upload_valid_processes_and_is_readable(client, fake_providers, dispatcher):
    _ready(fake_providers, fake_invoice())
    r = _upload(client, pdf_file("a.pdf"))
    assert r.status_code == 202
    [row] = r.json()
    assert row["filename"] == "a.pdf" and row["cached"] is False and row.get("error") is None
    assert row["status"] == "queued"
    assert dispatcher.enqueued == [row["document_id"]]

    doc = client.get(f"/api/documents/{row['document_id']}").json()
    assert doc["status"] in ("validated_ok", "needs_review")
    assert doc["events"][0]["node"] == "intake"
    assert doc["invoices"][0]["invoice_number"] == "INV-001"


def test_upload_same_file_is_cached_and_not_reprocessed(client, fake_providers, dispatcher):
    _ready(fake_providers, fake_invoice())
    first = _upload(client, pdf_file("a.pdf")).json()[0]
    llm = fake_providers["llm"]
    calls = len(llm.calls)
    second = _upload(client, pdf_file("again.pdf")).json()[0]
    assert second["cached"] is True
    assert second["document_id"] == first["document_id"]
    assert len(llm.calls) == calls
    assert len(dispatcher.enqueued) == 1


def test_per_file_errors_do_not_fail_the_batch(client, fake_providers, monkeypatch):
    monkeypatch.setenv("MAX_FILE_MB", "1")
    _ready(fake_providers, fake_invoice())
    big = ("files", ("big.pdf", b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024), "application/pdf"))
    exe = ("files", ("evil.pdf", b"MZ\x90\x00" + b"0" * 100, "application/pdf"))
    docx = ("files", ("x.docx", b"PK\x03\x04" + b"0" * 100, "application/octet-stream"))
    r = _upload(client, pdf_file("ok.pdf"), big, exe, docx)
    assert r.status_code == 202
    rows = {x["filename"]: x for x in r.json()}
    assert rows["ok.pdf"]["document_id"]
    assert rows["big.pdf"]["error"]["code"] == "TOO_LARGE" and rows["big.pdf"]["document_id"] is None
    assert rows["evil.pdf"]["error"]["code"] == "UNSUPPORTED_TYPE"
    assert rows["x.docx"]["error"]["code"] == "UNSUPPORTED_TYPE"


def test_too_many_files(client):
    r = _upload(client, *[pdf_file(f"{i}.pdf", number=f"N{i}") for i in range(21)])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "TOO_MANY_FILES"


def test_zero_files_is_422_with_envelope(client):
    r = client.post("/api/documents")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_enqueue_failure_keeps_document_queued(client, dispatcher):
    def boom(_id):
        raise ConnectionError("broker down")

    dispatcher.process = boom
    r = _upload(client, pdf_file("a.pdf"))
    assert r.status_code == 202
    got = client.get(f"/api/documents/{r.json()[0]['document_id']}").json()
    assert got["status"] == "queued"


def test_list_shape_filter_pagination_and_bad_sort(client, fake_providers):
    _ready(fake_providers, fake_invoice("A-1"), fake_invoice("B-1", "Beta"))
    _upload(client, pdf_file("a.pdf", number="A-1"))
    _upload(client, pdf_file("b.pdf", number="B-1", supplier="Beta"))
    r = client.get("/api/documents", params={"limit": 1, "sort": "filename:asc"})
    body = r.json()
    assert body["total"] == 2 and body["limit"] == 1 and body["offset"] == 0
    [item] = body["items"]
    assert item["filename"] == "a.pdf"
    assert {"id", "status", "current_node", "invoice_count", "issue_count", "created_at"} <= item.keys()
    assert client.get("/api/documents", params={"q": "b.p"}).json()["total"] == 1
    assert client.get("/api/documents", params={"status": "failed"}).json()["total"] == 0
    bad = client.get("/api/documents", params={"sort": "password:asc"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_SORT"


def test_get_missing_document_404_envelope(client):
    r = client.get("/api/documents/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/api/documents/not-a-uuid").status_code == 422


def test_pages_endpoint(client, fake_providers):
    _ready(fake_providers, fake_invoice())
    doc_id = _upload(client, pdf_file()).json()[0]["document_id"]
    pages = client.get(f"/api/documents/{doc_id}/pages").json()
    assert pages[0]["page_no"] == 0 and "INV-001" in pages[0]["text"]
    assert {"source", "ocr_conf"} <= pages[0].keys()


def test_file_endpoint_headers_and_sanitised_name(client, fake_providers):
    _ready(fake_providers, fake_invoice())
    evil = ("files", ('../../evil"\r\n.pdf', make_pdf(), "application/pdf"))
    doc_id = _upload(client, evil).json()[0]["document_id"]
    r = client.get(f"/api/documents/{doc_id}/file")
    assert r.status_code == 200
    assert r.content == make_pdf()
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"
    disp = r.headers["content-disposition"]
    assert disp.startswith("inline")
    assert "\r" not in disp and "\n" not in disp and ".." not in disp and "/" not in disp


def test_file_endpoint_missing_file_is_404(client, fake_providers, upload_dir):
    _ready(fake_providers, fake_invoice())
    doc_id = _upload(client, pdf_file()).json()[0]["document_id"]
    for f in upload_dir.iterdir():
        if f.is_file():
            f.unlink()
    assert client.get(f"/api/documents/{doc_id}/file").status_code == 404


def _set_status(session, doc_id, status):
    d = session.get(Document, doc_id)
    d.status = status
    session.commit()


@pytest.mark.parametrize("status", ["validated_ok", "queued", "processing"])
def test_reprocess_ineligible_409(client, fake_providers, session, status):
    _ready(fake_providers, fake_invoice())
    doc_id = _upload(client, pdf_file()).json()[0]["document_id"]
    _set_status(session, doc_id, status)
    r = client.post(f"/api/documents/{doc_id}/reprocess")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"


def test_reprocess_needs_review_requires_force(client, fake_providers, session, dispatcher):
    _ready(fake_providers, fake_invoice())
    doc_id = _upload(client, pdf_file()).json()[0]["document_id"]
    _set_status(session, doc_id, "needs_review")
    assert client.post(f"/api/documents/{doc_id}/reprocess").status_code == 409
    fake_providers["llm"].responses.append(fake_invoice())
    r = client.post(f"/api/documents/{doc_id}/reprocess", params={"force": "true"})
    assert r.status_code == 202
    assert r.json()["document_id"] == doc_id
    assert dispatcher.enqueued.count(doc_id) == 2
    detail = client.get(f"/api/documents/{doc_id}").json()
    assert detail["attempt"] == 2
    assert {e["attempt"] for e in detail["events"]} == {1, 2}


def test_reprocess_missing_404(client):
    r = client.post("/api/documents/00000000-0000-0000-0000-000000000000/reprocess")
    assert r.status_code == 404


def test_duplicate_across_two_documents_flagged(client, fake_providers):
    _ready(fake_providers, fake_invoice("DUP", "Dupe", "9.00"), fake_invoice("DUP", "Dupe", "9.00"))
    _upload(client, pdf_file("one.pdf", number="DUP", supplier="Dupe", total="9.00"))
    second = _upload(client, ("files", ("two.pdf", make_pdf("DUP", "Dupe", "9.00") + b"\n%x", "application/pdf")))
    detail = client.get(f"/api/documents/{second.json()[0]['document_id']}").json()
    codes = [i["code"] for i in detail["invoices"][0]["issues"]]
    assert "DUPLICATE_INVOICE" in codes
    assert detail["status"] == "needs_review"


def test_money_serialised_as_strings(client, fake_providers):
    _ready(fake_providers, fake_invoice(total="1234.50"))
    doc_id = _upload(client, pdf_file(total="1234.50")).json()[0]["document_id"]
    inv = client.get(f"/api/documents/{doc_id}").json()["invoices"][0]
    assert isinstance(inv["total"], str)
    assert inv["total"].startswith("1234.5")
