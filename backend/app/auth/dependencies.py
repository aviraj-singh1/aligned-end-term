from datetime import timedelta

from fastapi import HTTPException, Request
from sqlmodel import Session, select

from backend.app.db.models import AuthSession, User
from backend.app.db.session import get_database_engine
from .config import auth_settings
from .security import digest, utcnow


def require_signed_out(request: Request):
    """Account setup must never run in an existing authenticated browser session."""
    try:
        current_user(request)
    except HTTPException as error:
        if error.status_code == 401:
            return
        raise
    raise HTTPException(409, "You are already signed in. Account setup is for the invited colleague. Sign out before opening your own invitation; do not activate someone else's account.")


def current_user(request: Request) -> User:
    settings = auth_settings()
    token = request.cookies.get(settings.cookie_name, "")
    if not token or len(token) > 256:
        raise HTTPException(401, "Sign in to continue")
    now = utcnow()
    with Session(get_database_engine(), expire_on_commit=False) as db:
        session = db.exec(select(AuthSession).where(AuthSession.token_hash == digest(token)).with_for_update()).first()
        if not session or session.revoked_at or session.expires_at <= now or session.idle_expires_at <= now:
            raise HTTPException(401, "Your session has expired. Please sign in again.")
        user = db.get(User, session.user_id)
        if not user or user.status != "ACTIVE":
            raise HTTPException(401, "Sign in to continue")
        expected_user = request.headers.get('x-expected-user-id')
        if expected_user and expected_user != str(user.id):
            raise HTTPException(409, {
                'code': 'ACCOUNT_CHANGED',
                'message': 'Your signed-in account changed in another tab or window. Review the current account before trying again.',
            })
        session.last_seen_at = now
        session.idle_expires_at = min(session.expires_at, now + timedelta(seconds=settings.idle_seconds))
        db.add(session)
        db.commit()
        return user
