from datetime import timedelta

from fastapi import HTTPException
import pytest
import requests

from backend.app.auth import config
from backend.app.services import auth_service
from backend.app.services import msg91_phone_service as service


class Response:
    def __init__(self,status,payload): self.status_code=status; self.payload=payload
    def json(self):
        if isinstance(self.payload,Exception): raise self.payload
        return self.payload


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED','true')
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('AUTH_FRONTEND_URL','http://localhost:5173')
    monkeypatch.setenv('AUTH_SECRET','test-secret-' * 8)
    monkeypatch.setenv('MSG91_AUTH_KEY','server-secret')
    config.auth_settings.cache_clear()
    yield
    config.auth_settings.cache_clear()


def test_verifies_widget_token_and_extracts_nested_phone(monkeypatch):
    calls=[]
    monkeypatch.setattr(service.requests,'post',lambda *args,**kwargs: calls.append((args,kwargs)) or Response(200,{'type':'success','data':{'identifier':'919876543210'}}))
    assert service.verify_msg91_access_token('widget-jwt-token-value-long')=='+919876543210'
    assert calls[0][0]==(service.MSG91_VERIFY_ACCESS_TOKEN_URL,)
    assert calls[0][1]['json']=={'authkey':'server-secret','access-token':'widget-jwt-token-value-long'}
    assert calls[0][1]['timeout']==8


@pytest.mark.parametrize('response,status',[((401,{'type':'error','message':'invalid token'}),401),((200,{'type':'success'}),502),((200,ValueError('html')),502)])
def test_rejects_unverified_or_malformed_responses(monkeypatch,response,status):
    monkeypatch.setattr(service.requests,'post',lambda *a,**k:Response(*response))
    with pytest.raises(HTTPException) as exc: service.verify_msg91_access_token('bad-widget-token-value')
    assert exc.value.status_code==status
    assert 'bad-widget-token-value' not in exc.value.detail
    assert 'server-secret' not in exc.value.detail


def test_maps_provider_network_failure(monkeypatch):
    monkeypatch.setattr(service.requests,'post',lambda *a,**k:(_ for _ in ()).throw(requests.ConnectionError('secret transport detail')))
    with pytest.raises(HTTPException) as exc: service.verify_msg91_access_token('widget-token-value')
    assert exc.value.status_code==503 and 'secret transport detail' not in exc.value.detail


@pytest.mark.parametrize(('app_env','enabled','expected'), [
    ('development','true',True), ('development','false',False),
    ('production','true',True), ('production','false',False),
])
def test_phone_verification_skip_follows_explicit_flag(monkeypatch,app_env,enabled,expected):
    monkeypatch.setenv('APP_ENV',app_env)
    monkeypatch.setenv('ALLOW_PHONE_VERIFICATION_SKIP',enabled)
    assert auth_service._phone_verification_skip_allowed() is expected


def test_receipt_is_bound_to_subject_and_expires(monkeypatch):
    receipt=service.issue_phone_receipt('supabase-a','+919876543210')
    assert service.verify_phone_receipt(receipt,'supabase-a')=='+919876543210'
    with pytest.raises(HTTPException): service.verify_phone_receipt(receipt,'supabase-b')
    issued=service.utcnow()
    monkeypatch.setattr(service,'utcnow',lambda:issued+service.RECEIPT_TTL+timedelta(seconds=1))
    with pytest.raises(HTTPException): service.verify_phone_receipt(receipt,'supabase-a')


def test_missing_backend_key_is_configuration_error(monkeypatch):
    monkeypatch.delenv('MSG91_AUTH_KEY')
    with pytest.raises(HTTPException) as exc: service.verify_msg91_access_token('widget-token-value')
    assert exc.value.status_code==503
