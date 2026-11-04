"""GET /api/search."""
from __future__ import annotations

import pytest

from tests.api.helpers import fake_invoice, pdf_file
from tests.fakes import FailingEmbedder, FakeLLM


@pytest.fixture
def seeded(client, fake_providers):
    fake_providers["llm"] = FakeLLM(responses=[
        fake_invoice("A-1", "Acme Hydraulics", "100.00"),
        fake_invoice("Z-9", "Zeta Paper", "250.00"),
    ])
    client.post("/api/documents", files=[pdf_file("a.pdf", number="A-1", supplier="Acme Hydraulics")])
    client.post("/api/documents", files=[pdf_file("z.pdf", number="Z-9", supplier="Zeta Paper", total="250.00")])


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_modes_return_matching_invoice(client, seeded, mode):
    r = client.get("/api/search", params={"q": "Acme Hydraulics", "mode": mode})
    assert r.status_code == 200
    body = r.json()
    assert {"items", "total", "limit", "offset", "degraded"} <= body.keys()
    top = body["items"][0]
    assert "Acme" in (top["supplier_name"] or top["snippet"] or top["filename"])
    assert top["matched_by"] in ("keyword", "semantic", "both")


def test_invoice_hits_serialise_money_as_strings(client, seeded):
    r = client.get("/api/search", params={"q": "Acme Hydraulics", "mode": "keyword"})
    inv_hits = [i for i in r.json()["items"] if i["invoice_id"]]
    assert inv_hits and isinstance(inv_hits[0]["total"], str)


def test_filters_over_http(client, seeded):
    r = client.get("/api/search", params={"q": "Invoice", "mode": "keyword", "min_total": "200"})
    docs = {i["filename"] for i in r.json()["items"]}
    assert docs == {"z.pdf"}
    r = client.get("/api/search", params={"q": "Invoice", "mode": "keyword", "currency": "EUR"})
    assert r.json()["items"] == []


def test_validation(client):
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search", params={"q": "x" * 501}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "mode": "fuzzy"}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "limit": 101}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "min_total": "abc"}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "date_from": "yesterday"}).status_code == 422


def test_hybrid_degrades_when_embedder_down(client, seeded, fake_providers):
    fake_providers["embedder"] = FailingEmbedder()
    r = client.get("/api/search", params={"q": "Acme", "mode": "hybrid"})
    assert r.status_code == 200 and r.json()["degraded"] is True and r.json()["items"]


def test_semantic_503_when_embedder_down(client, seeded, fake_providers):
    fake_providers["embedder"] = FailingEmbedder()
    r = client.get("/api/search", params={"q": "Acme", "mode": "semantic"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "EMBEDDING_UNAVAILABLE"
