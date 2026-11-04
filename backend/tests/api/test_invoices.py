"""GET/PATCH /api/invoices/{id}."""
from __future__ import annotations

from app.db.models import Document
from tests.api.helpers import fake_invoice, pdf_file
from tests.fakes import FakeLLM


def _make(client, fake_providers, **amounts):
    """Upload one invoice whose page and extraction agree on `amounts` (subtotal/tax/total)."""
    # a validation error makes the pipeline retry once with vision: queue the answer twice
    fake_providers["llm"] = FakeLLM(responses=[fake_invoice(**amounts), fake_invoice(**amounts)])
    doc_id = client.post("/api/documents", files=[pdf_file(**amounts)]).json()[0]["document_id"]
    inv = client.get(f"/api/documents/{doc_id}").json()["invoices"][0]
    return doc_id, inv["id"]


def test_get_invoice_has_lines_issues_corrections(client, fake_providers):
    _, inv_id = _make(client, fake_providers)
    inv = client.get(f"/api/invoices/{inv_id}").json()
    assert inv["id"] == inv_id
    assert {"line_items", "issues", "corrections", "status"} <= inv.keys()


def test_get_invoice_404(client):
    assert client.get("/api/invoices/00000000-0000-0000-0000-000000000000").status_code == 404


def test_patch_fixes_total_and_recomputes_statuses(client, fake_providers):
    # 80 + 10 != 100: an arithmetic error that blocks validated_ok
    doc_id, inv_id = _make(client, fake_providers, subtotal="80.00", tax="10.00", total="100.00")
    assert client.get(f"/api/documents/{doc_id}").json()["status"] == "needs_review"

    r = client.patch(f"/api/invoices/{inv_id}", json={"fields": {"tax": "20.00"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["corrected"] is True
    assert body["status"] == "validated_ok"
    assert [c["field"] for c in body["corrections"]] == ["tax"]
    assert client.get(f"/api/documents/{doc_id}").json()["status"] == "validated_ok"


def test_patch_bad_values_report_every_field(client, fake_providers):
    _, inv_id = _make(client, fake_providers)
    r = client.patch(f"/api/invoices/{inv_id}",
                     json={"fields": {"total": "twelve", "currency": "EURO", "invoice_date": "03/04/2026"}})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert set(err["fields"]) == {"total", "currency", "invoice_date"}
    assert client.get(f"/api/invoices/{inv_id}").json()["corrected"] is False


def test_patch_unknown_field_422(client, fake_providers):
    _, inv_id = _make(client, fake_providers)
    r = client.patch(f"/api/invoices/{inv_id}", json={"fields": {"status": "validated_ok"}})
    assert r.status_code == 422
    assert "status" in r.json()["error"]["fields"]


def test_patch_rejects_extra_top_level_keys_and_non_strings(client, fake_providers):
    _, inv_id = _make(client, fake_providers)
    assert client.patch(f"/api/invoices/{inv_id}", json={"id": "x"}).status_code == 422
    assert client.patch(f"/api/invoices/{inv_id}", json={"fields": {"total": 12.5}}).status_code == 422


def test_patch_line_items(client, fake_providers):
    _, inv_id = _make(client, fake_providers)
    r = client.patch(f"/api/invoices/{inv_id}",
                     json={"line_items": [{"position": 1, "description": "Bolts", "amount": "100.00"}]})
    assert r.status_code == 200, r.text
    assert r.json()["line_items"][0]["description"] == "Bolts"
    assert r.json()["line_items"][0]["amount"] == "100.0000"


def test_patch_while_processing_409(client, fake_providers, session):
    doc_id, inv_id = _make(client, fake_providers)
    session.get(Document, doc_id).status = "processing"
    session.commit()
    r = client.patch(f"/api/invoices/{inv_id}", json={"fields": {"total": "1.00"}})
    assert r.status_code == 409


def test_patch_404(client):
    r = client.patch("/api/invoices/00000000-0000-0000-0000-000000000000", json={"fields": {"total": "1"}})
    assert r.status_code == 404
