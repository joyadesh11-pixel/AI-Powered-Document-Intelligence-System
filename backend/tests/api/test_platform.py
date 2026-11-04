"""Auth, CORS, headers, health, error envelope."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import get_redis
from app.api.main import create_app


def test_auth_off_by_default(client):
    assert client.get("/api/documents").status_code == 200


def test_auth_on(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers={"X-API-Key": "nope"}).status_code == 401
    r = client.get("/api/documents", headers={"X-API-Key": "s3cret"})
    assert r.status_code == 200
    denied = client.get("/api/documents")
    assert denied.json()["error"]["code"] == "UNAUTHORIZED"
    assert "s3cret" not in denied.text
    assert client.get("/api/health").status_code == 401   # Q1: health is authenticated


def test_preflight_passes_without_key(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    r = client.options("/api/documents", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "x-api-key"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_other_origins(client):
    r = client.get("/api/documents", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_security_headers(client):
    r = client.get("/api/documents")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_health_ok(client, redis_state):
    redis_state.depth = 3
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "redis": "ok", "queue_depth": 3}


def test_health_503_when_redis_down(client, redis_state):
    redis_state.up = False
    r = client.get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded" and body["db"] == "ok" and body["redis"] == "down"
    assert body["queue_depth"] is None


def test_unexpected_error_hides_internals(session_factory):
    app = create_app()

    def boom():
        raise RuntimeError("secret internal detail /etc/passwd")

    app.dependency_overrides[get_redis] = boom
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/health")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}


def test_unknown_route_uses_envelope(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_wrong_method_envelope(client):
    r = client.delete("/api/documents")
    assert r.status_code == 405 and "error" in r.json()
