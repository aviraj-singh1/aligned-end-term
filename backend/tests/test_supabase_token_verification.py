import pytest
from fastapi import HTTPException

from backend.app.auth import supabase


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def configure(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', 'publishable-key')


def configure_admin(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'backend-secret-key')


def test_verifies_access_token_with_supabase_auth_user_endpoint(monkeypatch):
    configure(monkeypatch)
    calls = []
    monkeypatch.setattr(supabase.requests, 'get', lambda *args, **kwargs: calls.append((args, kwargs)) or Response(payload={
        'id': 'supabase-user-id', 'email': 'owner@example.com',
    }))

    user = supabase.verify_supabase_token('access-token')

    assert user['id'] == 'supabase-user-id'
    assert calls[0][0][0] == 'https://project.supabase.co/auth/v1/user'
    assert calls[0][1]['headers'] == {'apikey': 'publishable-key', 'Authorization': 'Bearer access-token'}
    assert supabase.identity_issuer() == 'https://project.supabase.co/auth/v1'


@pytest.mark.parametrize('status', [401, 403])
def test_rejects_expired_or_invalid_supabase_token(monkeypatch, status):
    configure(monkeypatch)
    monkeypatch.setattr(supabase.requests, 'get', lambda *args, **kwargs: Response(status))
    with pytest.raises(HTTPException, match='expired') as error:
        supabase.verify_supabase_token('bad-token')
    assert error.value.status_code == 401


def test_reports_supabase_network_failure_without_certificate_specific_errors(monkeypatch):
    configure(monkeypatch)
    def unavailable(*args, **kwargs):
        raise supabase.requests.ConnectionError('offline')
    monkeypatch.setattr(supabase.requests, 'get', unavailable)
    with pytest.raises(HTTPException, match='temporarily unavailable') as error:
        supabase.verify_supabase_token('access-token')
    assert error.value.status_code == 503


def test_requires_supabase_configuration(monkeypatch):
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_PUBLISHABLE_KEY', raising=False)
    with pytest.raises(HTTPException, match='not configured') as error:
        supabase.verify_supabase_token('access-token')
    assert error.value.status_code == 503


def test_deletes_supabase_user_with_backend_secret(monkeypatch):
    configure_admin(monkeypatch)
    calls = []
    monkeypatch.setattr(supabase.requests, 'request',
        lambda *args, **kwargs: calls.append((args, kwargs)) or Response(204))
    supabase.delete_supabase_user('supabase-user-id')
    assert calls[0][0] == ('DELETE', 'https://project.supabase.co/auth/v1/admin/users/supabase-user-id')
    assert calls[0][1]['headers'] == {
        'apikey':'backend-secret-key', 'Authorization':'Bearer backend-secret-key'}


def test_account_administration_requires_backend_secret(monkeypatch):
    configure(monkeypatch)
    monkeypatch.delenv('SUPABASE_SECRET_KEY', raising=False)
    with pytest.raises(HTTPException, match='administration is not configured') as error:
        supabase.delete_supabase_user('supabase-user-id')
    assert error.value.status_code == 503
