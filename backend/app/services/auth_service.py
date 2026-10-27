from datetime import timedelta
import secrets
import re

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.api.auth_schemas import RegisterRequest, SupabaseRegisterRequest
from backend.app.auth.config import auth_settings
from backend.app.auth.mail import send_action_mail
from backend.app.auth.security import DUMMY_HASH, PASSWORD_HASHER, digest, utcnow, verify_password
from backend.app.db.models import (
    AuditEvent, AuthActionToken, AuthSecurityEvent, AuthSession, PasswordCredential,
    AuthIdentity, User, Workspace, WorkspaceMembership,
)
from backend.app.db.session import get_database_engine


def event(db, action, user_id=None, outcome="SUCCESS"):
    db.add(AuthSecurityEvent(user_id=user_id, action=action, outcome=outcome))


def action_token(db, user, purpose):
    token = secrets.token_urlsafe(32)
    db.add(AuthActionToken(user_id=user.id, purpose=purpose, email_normalized=user.email_normalized,
                           token_hash=digest(token), expires_at=utcnow() + timedelta(hours=24) if purpose == "VERIFY_EMAIL" else utcnow() + timedelta(minutes=30)))
    return token


def create_session(db, user_id, old_token=''):
    now, settings = utcnow(), auth_settings()
    if old_token:
        db.execute(update(AuthSession).where(AuthSession.token_hash == digest(old_token), AuthSession.revoked_at.is_(None)).values(revoked_at=now))
    raw = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user_id, token_hash=digest(raw), expires_at=now + timedelta(seconds=settings.absolute_seconds),
        idle_expires_at=now + timedelta(seconds=settings.idle_seconds), last_seen_at=now))
    return raw


def namespace(workspace):
    label = re.sub('[^a-z0-9]+', '-', workspace.name.lower()).strip('-')[:45] or 'firm'
    return label + '-' + workspace.id.hex[:12]


def register(data: RegisterRequest, old_token=''):
    encoded = PASSWORD_HASHER.hash(data.password)
    try:
        with Session(get_database_engine(), expire_on_commit=False) as db:
            if db.exec(select(User).where(User.email_normalized == data.email)).first():
                return
            user = User(name=data.name, email=str(data.email), email_normalized=str(data.email))
            db.add(user)
            db.flush()
            db.add(PasswordCredential(user_id=user.id, password_hash=encoded))
            workspace = Workspace(name=data.firm_name if data.workspace_type == "FIRM" else data.name,
                                  type=data.workspace_type, created_by=user.id,
                                  address_line1=data.address_line1, address_line2=data.address_line2,
                                  city=data.city, state=data.state, postal_code=data.postal_code,
                                  country_code=data.country_code)
            db.add(workspace)
            db.flush()
            if workspace.type == 'FIRM':
                workspace.login_namespace = namespace(workspace)
                db.add(workspace)
            db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="OWNER", joined_at=utcnow(),
                login_id=f'admin@{workspace.login_namespace}.aligned.com' if workspace.login_namespace else None))
            db.add(AuditEvent(workspace_id=workspace.id, actor_user_id=user.id, action="WORKSPACE_CREATED", entity_type="workspace", entity_id=workspace.id))
            event(db, "REGISTER", user.id)
            token = action_token(db, user, "VERIFY_EMAIL")
            session_token = create_session(db, user.id, old_token)
            db.commit()
    except IntegrityError as exc:
        # Concurrent duplicate signup is harmless; other constraint failures must surface.
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_users_email_normalized":
            return
        raise
    send_action_mail(user.email, "VERIFY_EMAIL", token)
    return session_token


def _supabase_provider(user: dict) -> str:
    metadata = user.get("app_metadata") or {}
    providers = {str(value).lower() for value in metadata.get("providers") or []}
    providers.add(str(metadata.get("provider") or "email").lower())
    return "SUPABASE_GOOGLE" if "google" in providers else "SUPABASE_EMAIL"


def supabase_login(raw_token: str, old_token: str):
    from backend.app.auth.supabase import identity_issuer, verify_supabase_token
    claims = verify_supabase_token(raw_token)
    issuer, subject = identity_issuer(), str(claims["id"])
    with Session(get_database_engine()) as db:
        identity = db.exec(select(AuthIdentity).where(
            AuthIdentity.issuer == issuer, AuthIdentity.subject == subject)).first()
        user = db.get(User, identity.user_id) if identity else None
        if not user or user.status != "ACTIVE":
            raise HTTPException(401, "No ALIGNED account is connected to this Supabase identity")
        provider = _supabase_provider(claims)
        if identity.provider != provider:
            identity.provider = provider
            db.add(identity)
        token = create_session(db, user.id, old_token)
        event(db, "LOGIN", user.id)
        db.commit()
        return token


def register_supabase(data: SupabaseRegisterRequest, old_token: str):
    from backend.app.auth.supabase import identity_issuer, verify_supabase_token
    claims = verify_supabase_token(data.access_token)
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(422, "Supabase did not provide a contact email")
    issuer, subject = identity_issuer(), str(claims["id"])
    provider = _supabase_provider(claims)
    email_confirmed = bool(claims.get("email_confirmed_at") or claims.get("confirmed_at"))
    if not email_confirmed:
        raise HTTPException(401, "Confirm your email with Supabase before creating a workspace")
    with Session(get_database_engine(), expire_on_commit=False) as db:
        if db.exec(select(AuthIdentity).where(
                AuthIdentity.issuer == issuer, AuthIdentity.subject == subject)).first():
            raise HTTPException(409, "This Supabase identity already has an ALIGNED account. Sign in instead.")
        if db.exec(select(User).where(User.email_normalized == email)).first():
            raise HTTPException(409, "An ALIGNED account already uses this email. Sign in instead.")
        user = User(name=data.name, email=email, email_normalized=email,
            email_verified_at=utcnow(), phone_number=None, phone_verified_at=None)
        db.add(user); db.flush()
        db.add(AuthIdentity(user_id=user.id, issuer=issuer, subject=subject, provider=provider))
        workspace = Workspace(name=data.firm_name if data.workspace_type == "FIRM" else data.name,
            type=data.workspace_type, created_by=user.id, address_line1=data.address_line1,
            address_line2=data.address_line2, city=data.city, state=data.state,
            postal_code=data.postal_code, country_code=data.country_code)
        db.add(workspace); db.flush()
        if workspace.type == "FIRM":
            workspace.login_namespace = namespace(workspace); db.add(workspace)
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="OWNER",
            joined_at=utcnow(), login_id=f"admin@{workspace.login_namespace}.aligned.com" if workspace.login_namespace else None))
        db.add(AuditEvent(workspace_id=workspace.id, actor_user_id=user.id,
            action="WORKSPACE_CREATED", entity_type="workspace", entity_id=workspace.id,
            event_metadata={"identity_provider": provider}))
        event(db, "REGISTER", user.id)
        session_token = create_session(db, user.id, old_token)
        db.commit()
    return session_token


def supabase_reauthenticate(user: User, raw_token: str | None) -> str | None:
    if not raw_token:
        return None
    from backend.app.auth.supabase import identity_issuer, verify_supabase_token
    claims = verify_supabase_token(raw_token)
    issuer, subject = identity_issuer(), str(claims["id"])
    with Session(get_database_engine()) as db:
        identity = db.exec(select(AuthIdentity).where(
            AuthIdentity.user_id == user.id,
            AuthIdentity.issuer == issuer,
            AuthIdentity.subject == subject,
        )).first()
    return subject if identity else None


def delete_supabase_identity(subject: str) -> None:
    from backend.app.auth.supabase import delete_supabase_user
    delete_supabase_user(subject)


def update_profile(user: User, name: str, email: str, current_password: str | None,
                   supabase_access_token: str | None = None):
    normalized = email.strip().lower()
    with Session(get_database_engine()) as db:
        current = db.get(User, user.id)
        if normalized.endswith(".aligned.com"):
            raise HTTPException(422, "Use your real contact email, not a generated firm login ID")
        if db.exec(select(User).where(User.email_normalized == normalized, User.id != user.id)).first():
            raise HTTPException(409, "Another ALIGNED account already uses this email")
        credential = db.exec(select(PasswordCredential).where(PasswordCredential.user_id == user.id)).first()
        email_changed = normalized != current.email_normalized
        external_identity = db.exec(select(AuthIdentity).where(AuthIdentity.user_id == user.id)).first()
        if email_changed and external_identity:
            raise HTTPException(409, "Change the sign-in email through Supabase account recovery support")
        password_ok = bool(credential and current_password and verify_password(credential.password_hash, current_password))
        if email_changed and not password_ok:
            raise HTTPException(403, "Confirm with your current password to change the contact email")
        current.name = name.strip()
        if email_changed:
            current.email = current.email_normalized = normalized
            current.email_verified_at = None
            db.execute(update(AuthActionToken).where(AuthActionToken.user_id == current.id,
                AuthActionToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
            verification = action_token(db, current, "VERIFY_EMAIL")
        else:
            verification = None
        db.add(current); db.commit()
    if verification:
        send_action_mail(normalized, "VERIFY_EMAIL", verification)
    return {"message": "Profile updated. Verify the new email before using protected workflows." if email_changed else "Profile updated"}


def login(email: str, password: str, old_token: str):
    with Session(get_database_engine()) as db:
        alias = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.login_id == email)).first()
        candidate = db.exec(select(User, PasswordCredential).join(PasswordCredential, PasswordCredential.user_id == User.id).where(User.id == alias.user_id if alias else User.email_normalized == email)).first()
        user_id, encoded, version = (candidate[0].id, candidate[1].password_hash, candidate[1].version) if candidate else (None, DUMMY_HASH, None)
    valid = verify_password(encoded, password)
    rehash = PASSWORD_HASHER.hash(password) if valid and user_id and PASSWORD_HASHER.check_needs_rehash(encoded) else None
    now = utcnow()
    settings = auth_settings()
    with Session(get_database_engine()) as db:
        credential = db.exec(select(PasswordCredential).where(PasswordCredential.user_id == user_id).with_for_update()).first() if user_id else None
        user = db.get(User, user_id) if user_id else None
        membership = db.get(WorkspaceMembership, alias.id) if alias else None
        pending = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user_id, WorkspaceMembership.activation_pending.is_(True))).first() if user_id else None
        alias_workspace = db.get(Workspace, membership.workspace_id) if membership else None
        if pending or (alias and (not membership or membership.status != 'ACTIVE' or not alias_workspace or alias_workspace.status != 'ACTIVE')):
            valid = False
        if not valid or not credential or credential.version != version or not user or user.status != "ACTIVE":
            event(db, "LOGIN", outcome="DENIED")
            db.commit()
            raise HTTPException(401, "Email or password is incorrect")
        if rehash:
            credential.password_hash = rehash
            db.add(credential)
        if old_token:
            db.execute(update(AuthSession).where(AuthSession.token_hash == digest(old_token), AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        token = secrets.token_urlsafe(32)
        db.add(AuthSession(user_id=user.id, token_hash=digest(token), expires_at=now + timedelta(seconds=settings.absolute_seconds),
                           idle_expires_at=now + timedelta(seconds=settings.idle_seconds), last_seen_at=now))
        event(db, "LOGIN", user.id)
        db.commit()
        return token


def logout(token: str, user_id=None):
    with Session(get_database_engine()) as db:
        query = update(AuthSession).where(AuthSession.revoked_at.is_(None))
        query = query.where(AuthSession.user_id == user_id) if user_id else query.where(AuthSession.token_hash == digest(token))
        db.execute(query.values(revoked_at=utcnow()))
        event(db, "LOGOUT_ALL" if user_id else "LOGOUT", user_id)
        db.commit()


def profile(user: User):
    from backend.app.services.owned_reconciliation import enabled as reconciliation_enabled
    with Session(get_database_engine()) as db:
        rows = db.exec(select(Workspace, WorkspaceMembership).join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id).where(
            WorkspaceMembership.user_id == user.id, WorkspaceMembership.status == "ACTIVE", Workspace.status == "ACTIVE"
        ).order_by(Workspace.created_at, Workspace.id)).all()
        identities = db.exec(select(AuthIdentity).where(AuthIdentity.user_id == user.id)).all()
        providers = {"google" if row.provider == "SUPABASE_GOOGLE" else "email" if row.provider == "SUPABASE_EMAIL" else "external" for row in identities}
        if user.phone_verified_at is not None:
            providers.add("phone")
        return {"user": {"id": str(user.id), "name": user.name, "email": user.email, "email_verified": user.email_verified_at is not None,
                         "phone_number": user.phone_number, "phone_verified": user.phone_verified_at is not None,
                         "identity_providers": sorted(providers)},
                "workspaces": [{"id": str(w.id), "name": w.name, "type": w.type, "role": m.role, 'login_id':m.login_id, 'login_namespace':w.login_namespace} for w, m in rows],
                "reconciliation_available": reconciliation_enabled()}


def request_email(email: str, purpose: str):
    with Session(get_database_engine(), expire_on_commit=False) as db:
        user = db.exec(select(User).where(User.email_normalized == email, User.status == "ACTIVE")).first()
        if not user or (purpose == "VERIFY_EMAIL" and user.email_verified_at):
            return
        if db.exec(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id, WorkspaceMembership.activation_pending.is_(True), WorkspaceMembership.status == 'ACTIVE')).first():
            purpose = 'ACTIVATE_ACCOUNT'
        # Do not invalidate previously delivered links on resend (avoids mail-order races).
        token = action_token(db, user, purpose)
        event(db, "REQUEST_EMAIL", user.id)
        db.commit()
    send_action_mail(user.email, purpose, token)


def consume_action(raw_token: str, purpose: str, password: str | None = None):
    if purpose == 'ACTIVATE_ACCOUNT':
        from .workspace_service import activate_account
        return activate_account(raw_token, password)
    encoded = PASSWORD_HASHER.hash(password) if password is not None else None
    now = utcnow()
    with Session(get_database_engine()) as db:
        # Read identity first, then consistently lock credential -> token -> user.
        candidate = db.exec(select(AuthActionToken).where(AuthActionToken.token_hash == digest(raw_token))).first()
        if not candidate:
            raise HTTPException(400, "This link is invalid or expired. Request a new one.")
        credential = db.exec(select(PasswordCredential).where(PasswordCredential.user_id == candidate.user_id).with_for_update()).first()
        token = db.exec(select(AuthActionToken).where(AuthActionToken.id == candidate.id).with_for_update().execution_options(populate_existing=True)).one()
        user = db.exec(select(User).where(User.id == token.user_id).with_for_update()).first()
        requires_password_credential = purpose != "VERIFY_EMAIL"
        if ((requires_password_credential and not credential) or not user or user.status != "ACTIVE"
                or token.purpose != purpose or token.consumed_at or token.revoked_at
                or token.expires_at <= now or token.email_normalized != user.email_normalized):
            raise HTTPException(400, "This link is invalid or expired. Request a new one.")
        token.consumed_at = now
        db.add(token)
        if purpose == "VERIFY_EMAIL":
            user.email_verified_at = now
            db.add(user)
        else:
            credential.password_hash = encoded
            credential.version += 1
            db.add(credential)
            db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        db.execute(update(AuthActionToken).where(AuthActionToken.user_id == user.id, AuthActionToken.purpose == purpose,
                                               AuthActionToken.id != token.id, AuthActionToken.revoked_at.is_(None)).values(revoked_at=now))
        event(db, purpose, user.id)
        db.commit()
