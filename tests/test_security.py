import pytest

from app.config import get_settings
from app.security import upload_limiter

PDF = b"%PDF-1.4\n%fake"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    upload_limiter.reset()
    monkeypatch.setattr(get_settings(), "demo_api_key", "secret")
    monkeypatch.setattr(get_settings(), "environment", "production")
    yield
    upload_limiter.reset()


def _upload(client, data=b"x", headers=None):
    return client.post(
        "/invoices/upload", files={"file": ("a.pdf", data, "application/pdf")}, headers=headers
    )


def test_upload_requires_key(client):
    assert _upload(client).status_code == 401
    assert _upload(client, headers={"X-API-Key": "wrong"}).status_code == 401


def test_upload_with_key_passes_auth(client):
    # past auth: an unsupported body gives 415 rather than 401
    assert _upload(client, headers={"X-API-Key": "secret"}).status_code == 415


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/reviews"),
        ("get", "/reviews/x"),
        ("post", "/reviews/x/approve"),
        ("post", "/reviews/x/correct"),
        ("post", "/reviews/x/reject"),
    ],
)
def test_review_routes_require_key(client, method, path):
    assert getattr(client, method)(path).status_code == 401
    kwargs = {"json": {"fields": {}}} if method == "post" else {}
    r = getattr(client, method)(path, headers={"X-API-Key": "secret"}, **kwargs)
    assert r.status_code in (200, 404)  # authenticated: list ok, unknown job not found


def test_health_and_docs_open(client):
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_unconfigured_key_fails_closed_outside_dev(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_api_key", "")
    assert _upload(client).status_code == 503
    monkeypatch.setattr(get_settings(), "environment", "dev")
    assert _upload(client).status_code == 415  # dev: auth off


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 100)
    r = _upload(client, PDF + b"0" * 200, headers={"X-API-Key": "secret"})
    assert r.status_code == 413


def test_upload_rate_limit_per_ip(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_rate_limit", 2)
    h = {"X-API-Key": "secret"}
    assert _upload(client, headers=h).status_code == 415
    assert _upload(client, headers=h).status_code == 415
    r = _upload(client, headers=h)
    assert r.status_code == 429
    # a different client IP has its own budget
    other = _upload(client, headers={**h, "X-Forwarded-For": "9.9.9.9"})
    assert other.status_code == 415


def test_failed_auth_does_not_consume_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_rate_limit", 1)
    for _ in range(3):
        assert _upload(client).status_code == 401
    assert _upload(client, headers={"X-API-Key": "secret"}).status_code == 415
