from datetime import timedelta
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from backend.tests.test_database_schema import schema_engine  # Reuse the guarded disposable-Postgres fixture.
from backend.app.api.routes.auth import router
from backend.app.auth import config, dependencies, security
from backend.app.auth.middleware import authentication_boundary
from backend.app.db.models import AuthActionToken, AuthIdentity, AuthSecurityEvent, AuthSession, PasswordCredential, User, Workspace, WorkspaceMembership
from backend.app.main import safe_validation_errors
from backend.app.services import auth_service
from backend.app.services import workspace_service
from backend.app.services import ownership_service
from backend.app.services import data_management_service
from backend.app.api.routes.workspaces import router as workspace_router

PASSWORD = "A proper sample passphrase 42!"
ORIGIN = "http://127.0.0.1:5173"
pytestmark = pytest.mark.postgres


@pytest.fixture
def auth_api(schema_engine, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-" * 8)
    monkeypatch.setenv("AUTH_FRONTEND_URL", ORIGIN)
    config.auth_settings.cache_clear()
    security.limiter.entries.clear()
    monkeypatch.setattr(auth_service, "get_database_engine", lambda: schema_engine)
    monkeypatch.setattr(dependencies, "get_database_engine", lambda: schema_engine)
    monkeypatch.setattr(workspace_service, "get_database_engine", lambda: schema_engine)
    mail = []
    monkeypatch.setattr(auth_service, "send_action_mail", lambda email, purpose, token: mail.append((email, purpose, token)))
    monkeypatch.setattr(workspace_service, "send_action_mail", lambda email, purpose, token: mail.append((email, purpose, token)) or True)
    monkeypatch.setattr(ownership_service, "send_action_mail", lambda email, purpose, token: mail.append((email, purpose, token)) or True)
    monkeypatch.setattr(data_management_service, "get_database_engine", lambda: schema_engine)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(workspace_router, prefix="/api")
    app.middleware("http")(authentication_boundary)
    app.exception_handler(RequestValidationError)(safe_validation_errors)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Origin": ORIGIN}) as client:
        yield client, mail, schema_engine
    config.auth_settings.cache_clear()
    security.limiter.entries.clear()


def post(client, path, data=None):
    csrf = client.get("/api/auth/csrf")
    assert csrf.status_code == 200
    return client.post("/api/auth/" + path, json=data or {}, headers={"X-CSRF-Token": csrf.json()["csrf_token"]})


def signup(client, kind="INDIVIDUAL"):
    data = {"name": "Sample User", "email": f"sample-{uuid4()}@example.com", "password": PASSWORD, "workspace_type": kind}
    if kind == "FIRM": data["firm_name"] = "Sample firm"
    assert post(client, "register", data).status_code == 202
    return data


@pytest.mark.parametrize("kind", ["INDIVIDUAL", "FIRM"])
def test_registration_login_verification_logout(auth_api, kind):
    client, mail, engine = auth_api
    data = signup(client, kind)
    assert len(mail) == 1
    assert client.get("/api/me").status_code == 200
    assert client.get('/api/me').json()['user']['email_verified'] is False
    resent = post(client, 'verification/resend', {})
    assert resent.status_code == 202 and resent.json()['address'] == data['email']
    assert mail[-1][0] == data['email'] and mail[-1][1] == 'VERIFY_EMAIL'
    with Session(engine) as db:
        user = db.exec(select(User).where(User.email_normalized == data["email"])).one()
        credential = db.exec(select(PasswordCredential).where(PasswordCredential.user_id == user.id)).one()
        assert credential.password_hash.startswith("$argon2id$")
        assert PASSWORD not in credential.password_hash
        workspace = db.exec(select(Workspace).where(Workspace.created_by == user.id)).one()
        assert workspace.type == kind
        membership = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)).one()
        assert membership.role == "OWNER"
        token = db.exec(select(AuthActionToken).where(AuthActionToken.token_hash == security.digest(mail[-1][2]))).one()
        assert token.token_hash == security.digest(mail[-1][2])
    login_id = client.get('/api/me').json()['workspaces'][0]['login_id']
    if kind == 'FIRM':
        assert login_id.startswith('admin@sample-firm-') and login_id.endswith('.aligned.com')
    login = post(client, "login", {"email": login_id or data["email"], "password": PASSWORD})
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"] and "SameSite=lax" in login.headers["set-cookie"]
    assert client.get("/api/me").json()["user"]["email_verified"] is False
    assert post(client, "verification/confirm", {"token": mail[-1][2]}).status_code == 200
    profile = client.get("/api/me").json()
    assert profile["user"]["email_verified"] is True
    assert profile["workspaces"][0]["type"] == kind
    assert profile["reconciliation_available"] is False
    assert "password_hash" not in str(profile)
    old_token = client.cookies.get("aligned_dev_session")
    assert post(client, "logout").status_code == 200
    client.cookies.set("aligned_dev_session", old_token)
    assert client.get("/api/me").status_code == 401


def test_passwordless_external_user_can_verify_email(auth_api):
    client, _, engine = auth_api
    email = f"external-passwordless-{uuid4()}@example.com"
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="External user", email=email, email_normalized=email)
        db.add(user)
        db.flush()
        token = auth_service.action_token(db, user, "VERIFY_EMAIL")
        user_id = user.id
        db.commit()

    response = post(client, "verification/confirm", {"token": token})
    assert response.status_code == 200
    with Session(engine) as db:
        verified = db.get(User, user_id)
        assert verified.email_verified_at is not None
        assert db.exec(select(PasswordCredential).where(
            PasswordCredential.user_id == user_id)).first() is None


def test_duplicate_registration_is_generic_and_does_not_create_extra_workspace(auth_api):
    client, mail, engine = auth_api
    data = signup(client)
    duplicate = post(client, "register", {**data, "email": data["email"].upper()})
    assert duplicate.status_code == 202 and len(mail) == 1
    with Session(engine) as db:
        user = db.exec(select(User).where(User.email_normalized == data["email"])).one()
        assert len(db.exec(select(Workspace).where(Workspace.created_by == user.id)).all()) == 1


def test_supabase_registration_and_login_are_independent_of_database_host(auth_api, monkeypatch):
    client, _, engine = auth_api
    from backend.app.auth import supabase
    monkeypatch.setattr(supabase, 'identity_issuer', lambda: 'https://project.supabase.co/auth/v1')
    monkeypatch.setattr(supabase, 'verify_supabase_token', lambda token: {
        'id':'supabase-user-1', 'email':'owner@supabase.example',
        'email_confirmed_at':'2026-09-09T00:00:00Z',
        'app_metadata':{'provider':'google'},
    })
    payload = {'access_token':'x' * 120, 'name':'Supabase Owner',
        'workspace_type':'INDIVIDUAL', 'address_line1':'10 Main Road',
        'city':'Delhi', 'state':'Delhi', 'postal_code':'110001', 'country_code':'IN'}

    registered = post(client, 'supabase/register', payload)
    assert registered.status_code == 201
    with Session(engine) as db:
        user = db.exec(select(User).where(User.email_normalized == 'owner@supabase.example')).one()
        identity = db.exec(select(AuthIdentity).where(AuthIdentity.user_id == user.id)).one()
        assert (identity.issuer, identity.subject, identity.provider) == (
            'https://project.supabase.co/auth/v1', 'supabase-user-1', 'SUPABASE_GOOGLE')
        assert user.phone_number is None and user.phone_verified_at is None
        assert db.exec(select(PasswordCredential).where(PasswordCredential.user_id == user.id)).first() is None

    assert post(client, 'logout').status_code == 200
    assert post(client, 'supabase/login', {'access_token':'y' * 120}).status_code == 200
    assert client.get('/api/me').json()['user']['identity_providers'] == ['google']


def test_supabase_registration_requires_confirmed_email(auth_api, monkeypatch):
    client, _, _ = auth_api
    from backend.app.auth import supabase
    monkeypatch.setattr(supabase, 'identity_issuer', lambda: 'https://project.supabase.co/auth/v1')
    monkeypatch.setattr(supabase, 'verify_supabase_token', lambda token: {
        'id':'supabase-user-unconfirmed', 'email':'unconfirmed@supabase.example',
        'app_metadata':{'provider':'email'},
    })
    response = post(client, 'supabase/register', {'access_token':'x' * 120,
        'name':'Unconfirmed', 'workspace_type':'INDIVIDUAL', 'address_line1':'10 Main Road',
        'city':'Delhi', 'state':'Delhi', 'postal_code':'110001', 'country_code':'IN'})
    assert response.status_code == 401
    assert 'confirm your email' in response.json()['detail'].lower()


@pytest.mark.parametrize('app_env', ['development', 'production'])
def test_supabase_registration_never_requires_phone(auth_api, monkeypatch, app_env):
    client, _, engine = auth_api
    from backend.app.auth import supabase
    monkeypatch.setenv('APP_ENV', app_env)
    monkeypatch.setattr(supabase, 'identity_issuer', lambda: 'https://project.supabase.co/auth/v1')
    monkeypatch.setattr(supabase, 'verify_supabase_token', lambda token: {
        'id':f'supabase-no-phone-{app_env}', 'email':f'no-phone-{app_env}@supabase.example',
        'email_confirmed_at':'2026-09-09T00:00:00Z', 'app_metadata':{'provider':'google'},
    })
    response = post(client, 'supabase/register', {'access_token':'x' * 120,
        'name':'No Phone Required', 'workspace_type':'INDIVIDUAL', 'address_line1':'10 Main Road',
        'city':'Delhi', 'state':'Delhi', 'postal_code':'110001', 'country_code':'IN'})
    assert response.status_code == 201
    with Session(engine) as db:
        user = db.exec(select(User).where(
            User.email_normalized == f'no-phone-{app_env}@supabase.example')).one()
        assert user.phone_number is None
        assert user.phone_verified_at is None
    assert client.get('/api/me').json()['user']['identity_providers'] == ['google']


def test_supabase_email_registration_uses_confirmed_supabase_identity(auth_api, monkeypatch):
    client, mail, engine = auth_api
    from backend.app.auth import supabase
    monkeypatch.setattr(supabase, 'identity_issuer', lambda: 'https://project.supabase.co/auth/v1')
    monkeypatch.setattr(supabase, 'verify_supabase_token', lambda token: {
        'id':'supabase-email-user', 'email':'local-mail@supabase.example',
        'email_confirmed_at':'2026-09-09T00:00:00Z',
        'app_metadata':{'provider':'email','providers':['email']},
    })
    response = post(client, 'supabase/register', {'access_token':'x' * 120,
        'name':'Supabase Email Owner', 'workspace_type':'INDIVIDUAL',
        'address_line1':'10 Main Road', 'city':'Delhi', 'state':'Delhi',
        'postal_code':'110001', 'country_code':'IN'})
    assert response.status_code == 201
    assert not mail
    assert client.get('/api/me').json()['user']['email_verified'] is True
    with Session(engine) as db:
        identity = db.exec(select(AuthIdentity).where(
            AuthIdentity.subject == 'supabase-email-user')).one()
        assert identity.provider == 'SUPABASE_EMAIL'


def test_msg91_endpoint_is_not_exposed(auth_api):
    client, _, _ = auth_api
    response = post(client, 'msg91/verify-phone', {
        'access_token':'x' * 120, 'msg91_access_token':'y' * 120,
    })
    assert response.status_code == 404


def test_csrf_origin_session_binding_and_request_limits(auth_api):
    client, _, _ = auth_api
    assert client.get("/api/auth/csrf", headers={"Origin": ORIGIN}).status_code == 200
    assert client.post("/api/auth/login", json={}).status_code == 403
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    assert client.post("/api/auth/logout", json={}, headers={"Origin": "http://evil.example", "X-CSRF-Token": token}).status_code == 403
    client.cookies.set("aligned_dev_session", "another-session")
    assert client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": token}).status_code == 403
    assert client.get("/api/auth/csrf", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/auth/csrf", headers={"Origin": "http://localhost:5174"}).status_code == 403
    assert client.post("/api/auth/login", content="x" * 9000).status_code == 413


def test_validation_redacts_secrets_and_rejects_privilege_fields(auth_api):
    client, _, _ = auth_api
    data = {"name": "Name", "email": "bad-email", "password": PASSWORD, "workspace_type": "FIRM", "role": "ADMIN"}
    response = post(client, "register", data)
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert '"input"' not in response.text


def test_login_failure_is_generic_and_disabled_user_is_denied(auth_api):
    client, _, engine = auth_api
    data = signup(client)
    unknown = post(client, "login", {"email": "missing@example.com", "password": PASSWORD})
    wrong = post(client, "login", {"email": data["email"], "password": "wrong password"})
    with Session(engine) as db:
        user = db.exec(select(User).where(User.email_normalized == data["email"])).one()
        user.status = "DISABLED"
        db.add(user)
        db.commit()
    disabled = post(client, "login", {"email": data["email"], "password": PASSWORD})
    assert unknown.status_code == wrong.status_code == disabled.status_code == 401
    assert unknown.json() == wrong.json() == disabled.json()


def test_reset_is_single_use_and_revokes_sessions(auth_api):
    client, mail, _ = auth_api
    data = signup(client)
    assert post(client, "login", {"email": data["email"], "password": PASSWORD}).status_code == 200
    assert post(client, "password/forgot", {"email": data["email"]}).status_code == 202
    token = mail[-1][2]
    payload = {"token": token, "password": "A different sample passphrase 84!"}
    assert post(client, "verification/confirm", {"token": token}).status_code == 400
    assert post(client, "password/reset", payload).status_code == 200
    assert client.get("/api/me").status_code == 401
    assert post(client, "password/reset", payload).status_code == 400
    assert post(client, "login", {"email": data["email"], "password": PASSWORD}).status_code == 401
    assert post(client, "login", {"email": data["email"], "password": payload["password"]}).status_code == 200


def test_expired_idle_session_and_action_tokens(auth_api, monkeypatch):
    client, mail, _ = auth_api
    data = signup(client)
    assert post(client, "login", {"email": data["email"], "password": PASSWORD}).status_code == 200
    future = security.utcnow() + timedelta(days=2)
    monkeypatch.setattr(dependencies, "utcnow", lambda: future)
    monkeypatch.setattr(auth_service, "utcnow", lambda: future)
    assert client.get("/api/me").status_code == 401
    assert post(client, "verification/confirm", {"token": mail[0][2]}).status_code == 400


@pytest.mark.parametrize("method,path", [("POST", "/api/inspect"), ("POST", "/api/reconcile"), ("POST", "/api/manual-match"),
    ("GET", "/api/session/foreign/results"), ("GET", "/api/session/foreign"), ("DELETE", "/api/session/foreign"),
    ("GET", "/api/export/foreign/excel"), ("GET", "/api/export/foreign/notes/excel"),
    ("GET", "/api/export/foreign/json")])
def test_no_financial_endpoint_is_exposed_in_partial_auth_mode(auth_api, method, path):
    response = auth_api[0].request(method, path)
    assert response.status_code == 503
    assert "authorization is complete" in response.json()["detail"]


def test_rate_limit_precedes_password_hashing(auth_api, monkeypatch):
    client, _, _ = auth_api
    called = []
    monkeypatch.setattr(auth_service, "login", lambda *args: called.append(True))
    for _ in range(15): security.limiter.check("account:/api/auth/login:" + security.digest("limited@example.com"))
    response = post(client, "login", {"email": "limited@example.com", "password": PASSWORD})
    assert response.status_code == 429
    assert 1 <= int(response.headers['Retry-After']) <= 300
    assert not called


def test_rate_limit_expires_individual_attempts_without_extending_on_rejection(monkeypatch):
    from fastapi import HTTPException
    clock = [0.0]
    monkeypatch.setattr(security.time, 'monotonic', lambda: clock[0])
    limiter = security.LocalRateLimiter()
    limiter.check('login', 2)
    clock[0] = 100
    limiter.check('login', 2)
    clock[0] = 299
    with pytest.raises(HTTPException) as caught:
        limiter.check('login', 2)
    assert caught.value.headers['Retry-After'] == '1'
    clock[0] = 300
    limiter.check('login', 2)  # First attempt expired, even though the second is recent.
    assert list(limiter.entries['login']) == [100, 300]
    clock[0] = 301
    with pytest.raises(HTTPException) as caught:
        limiter.check('login', 2)
    assert caught.value.headers['Retry-After'] == '99'


def test_csrf_and_verification_activity_do_not_consume_login_budget(auth_api, monkeypatch):
    client, _, _ = auth_api
    monkeypatch.setattr(auth_service, 'login', lambda *args: 'synthetic-session')
    for _ in range(75):
        assert client.get('/api/auth/csrf').status_code == 200
    for _ in range(15):
        security.limiter.check('account:/api/auth/verification/request:' + security.digest('limited@example.com'))
    assert post(client, 'login', {'email':'limited@example.com','password':PASSWORD}).status_code == 200


def test_one_account_limit_does_not_exhaust_another_account_on_same_ip(auth_api, monkeypatch):
    client, _, _ = auth_api
    monkeypatch.setattr(auth_service, 'login', lambda *args: 'synthetic-session')
    for _ in range(15):
        security.limiter.check('account:/api/auth/login:' + security.digest('admin@example.com'))
    assert post(client, 'login', {'email':'admin@example.com','password':PASSWORD}).status_code == 429
    assert post(client, 'login', {'email':'accountant@example.com','password':PASSWORD}).status_code == 200


def test_login_ip_flood_limit_remains_enforced(auth_api, monkeypatch):
    client, _, _ = auth_api
    called = []
    monkeypatch.setattr(auth_service, 'login', lambda *args: called.append(True))
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    # TestClient's peer is testclient; forwarded headers do not change its bucket.
    for _ in range(60):
        security.limiter.check('ip:/api/auth/login:testclient', 60)
    response = client.post('/api/auth/login', json={'email':'new@example.com','password':PASSWORD},
        headers={'X-CSRF-Token':csrf, 'X-Forwarded-For':'203.0.113.99'})
    assert response.status_code == 429
    assert not called


def test_security_events_are_append_only(auth_api):
    _, _, engine = auth_api
    with engine.begin() as connection:
        with pytest.raises(DBAPIError, match="append-only"):
            connection.execute(text("TRUNCATE auth_security_events"))


def test_production_auth_requires_https_and_uses_cross_site_secure_cookies(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_SECRET", "a" * 64)
    monkeypatch.setenv("AUTH_FRONTEND_URL", "https://aligned.example.com")
    config.auth_settings.cache_clear()
    settings = config.auth_settings()
    assert settings.frontend_url == "https://aligned.example.com"
    assert settings.secure_cookies is True
    assert settings.cookie_samesite == "none"
    assert settings.cookie_name == "aligned_session"
    config.auth_settings.cache_clear()


def test_concurrent_registration_creates_one_workspace(auth_api):
    from concurrent.futures import ThreadPoolExecutor
    from backend.app.api.auth_schemas import RegisterRequest
    _, _, engine = auth_api
    data = RegisterRequest(name="Concurrent signup", email=f"race-{uuid4()}@example.com", password=PASSWORD, workspace_type="INDIVIDUAL")
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(auth_service.register, [data, data]))
    with Session(engine) as db:
        user = db.exec(select(User).where(User.email_normalized == data.email)).one()
        assert len(db.exec(select(Workspace).where(Workspace.created_by == user.id)).all()) == 1


def test_concurrent_reset_consumes_token_once(auth_api):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    client, mail, _ = auth_api
    data = signup(client)
    post(client, "password/forgot", {"email": data["email"]})
    token = mail[-1][2]
    def consume(_):
        try:
            auth_service.consume_action(token, "RESET_PASSWORD", "Another long sample password 84!")
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(consume, range(2))) == [200, 400]


@pytest.mark.parametrize('purpose,route', [('VERIFY_EMAIL', 'verify-email'), ('RESET_PASSWORD', 'reset-password'), ('WORKSPACE_INVITE', 'accept-invite')])
def test_local_mail_has_readable_exact_link(tmp_path, monkeypatch, purpose, route):
    from types import SimpleNamespace
    from email import policy
    from email.parser import BytesParser
    from backend.app.auth import mail
    monkeypatch.setattr(mail, 'auth_settings', lambda: SimpleNamespace(frontend_url=ORIGIN, mail_dir=tmp_path))
    token = 'a' * 41 + '-_'
    assert mail.send_action_mail('example@example.com', purpose, token)
    text_file, = tmp_path.glob('*.txt')
    eml_file, = tmp_path.glob('*.eml')
    expected = f'{ORIGIN}/#/{route}?token={token}'
    plain = text_file.read_text(encoding='utf-8')
    assert expected in plain.splitlines()
    assert '=3D' not in plain and '=\n' not in plain
    assert text_file.stem == eml_file.stem
    decoded = BytesParser(policy=policy.default).parsebytes(eml_file.read_bytes()).get_content()
    assert expected in decoded.splitlines()
