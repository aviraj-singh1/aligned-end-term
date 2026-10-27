from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import hmac
import math
import secrets
from threading import Lock
import time
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response

from .config import auth_settings

PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def utcnow():
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(encoded: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def signature(value: str) -> str:
    return hmac.new(auth_settings().secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def validate_origin(request: Request):
    try:
        configured = urlsplit(auth_settings().frontend_url)
        supplied = urlsplit(request.headers.get("origin", ""))
        same_local_frontend = (
            supplied.scheme == configured.scheme
            and supplied.hostname == configured.hostname
            and supplied.port == configured.port
            and not supplied.path
            and not supplied.query
            and not supplied.fragment
            and not supplied.username
            and not supplied.password
        )
    except ValueError:
        same_local_frontend = False
    if not same_local_frontend:
        raise HTTPException(403, "Untrusted request origin")


def issue_csrf(request: Request, response: Response) -> str:
    settings = auth_settings()
    nonce = f"{int(time.time())}.{secrets.token_urlsafe(32)}"
    signed = f"{nonce}.{signature('preauth:' + nonce)}"
    response.set_cookie(
        settings.csrf_cookie_name, signed, httponly=True,
        secure=settings.secure_cookies, samesite=settings.cookie_samesite,
        max_age=3600, path="/",
    )
    session = request.cookies.get(settings.cookie_name, "")
    return signature(f"csrf:{signed}:{session}")


def validate_csrf(request: Request):
    validate_origin(request)
    settings = auth_settings()
    signed = request.cookies.get(settings.csrf_cookie_name, "")
    try:
        timestamp, nonce, mac = signed.split(".")
        age = time.time() - int(timestamp)
        valid = 0 <= age <= 3600 and hmac.compare_digest(mac, signature(f"preauth:{timestamp}.{nonce}"))
    except (ValueError, TypeError):
        valid = False
    expected = signature(f"csrf:{signed}:{request.cookies.get(settings.cookie_name, '')}")
    if not valid or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
        raise HTTPException(403, "Request verification expired. Please try again.")


class LocalRateLimiter:
    """Single-process development only; production activation is rejected."""
    def __init__(self):
        self.entries = defaultdict(deque)
        self.lock = Lock()

    def check(self, key: str, limit: int = 15, seconds: int = 300):
        now = time.monotonic()
        with self.lock:
            # Drop idle buckets, then expire individual attempts in this rolling window.
            for stale in [k for k, values in self.entries.items() if not values or values[-1] <= now - seconds]:
                del self.entries[stale]
            values = self.entries[key]
            while values and values[0] <= now - seconds:
                values.popleft()
            if len(values) >= limit:
                retry_after = max(1, math.ceil(values[0] + seconds - now))
                raise HTTPException(429, "Too many attempts. Please try again later.", headers={"Retry-After": str(retry_after)})
            values.append(now)


limiter = LocalRateLimiter()


def limit_auth(request: Request, email: str | None = None):
    # Route templates bound the key space and isolate unrelated auth/workspace actions.
    action = request.url.path if request.url.path.startswith('/api/auth/') else getattr(request.scope.get('route'), 'path', request.url.path)
    # Do not trust forwarded IP headers from an unconfigured proxy.
    # CSRF bootstrap is used by every form, not just sign-in; keep its own bounded budget.
    limiter.check(f"ip:{action}:{request.client.host if request.client else 'unknown'}", 300 if action == '/api/auth/csrf' else 60)
    if email:
        limiter.check(f"account:{action}:{digest(email.strip().lower())}", 15)
