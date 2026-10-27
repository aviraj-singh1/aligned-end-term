from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest

from backend.app.api.routes.auth import router
from backend.app.auth import config, security
from backend.app.auth.middleware import authentication_boundary
from backend.app.main import safe_validation_errors
from backend.app.services import auth_service


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-" * 8)
    monkeypatch.setenv("AUTH_FRONTEND_URL", "http://localhost:5173")
    config.auth_settings.cache_clear()
    security.limiter.entries.clear()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.middleware("http")(authentication_boundary)
    app.exception_handler(RequestValidationError)(safe_validation_errors)
    with TestClient(app, base_url="http://localhost:8000", headers={"Origin": "http://localhost:5173"}) as value:
        yield value
    config.auth_settings.cache_clear()
    security.limiter.entries.clear()


def test_phone_verification_endpoint_binds_both_tokens(client, monkeypatch):
    calls = []
    monkeypatch.setattr(auth_service, "verify_msg91_phone", lambda supabase, msg91: calls.append((supabase, msg91)) or {
        "phone_number": "+919876543210", "phone_verification_receipt": "signed-receipt",
    })
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    response = client.post("/api/auth/msg91/verify-phone", json={
        "access_token": "s" * 40, "msg91_access_token": "m" * 40,
    }, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {"phone_number": "+919876543210", "phone_verification_receipt": "signed-receipt"}
    assert calls == [("s" * 40, "m" * 40)]


def test_phone_verification_validation_never_echoes_tokens(client):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    token = "sensitive-token-value"
    msg91_token = "private-msg91-value"
    response = client.post("/api/auth/msg91/verify-phone", json={
        "access_token": token, "msg91_access_token": msg91_token,
    }, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422
    assert token not in response.text and msg91_token not in response.text
