from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import urlsplit


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


@dataclass(frozen=True)
class AuthSettings:
    secret: str = field(repr=False)
    frontend_url: str
    mail_dir: Path
    cookie_name: str = "aligned_dev_session"
    csrf_cookie_name: str = "aligned_dev_csrf"
    idle_seconds: int = 1800
    absolute_seconds: int = 43200
    secure_cookies: bool = False
    cookie_samesite: str = "lax"


@lru_cache(maxsize=1)
def auth_settings() -> AuthSettings:
    if not auth_enabled():
        raise RuntimeError("Authentication is disabled")
    production = bool(os.getenv("VERCEL")) or os.getenv("APP_ENV", "").lower() == "production"
    frontend = os.getenv("AUTH_FRONTEND_URL", "").rstrip("/")
    parsed = urlsplit(frontend)
    valid_origin = (
        parsed.scheme in ({"https"} if production else {"http", "https"})
        and bool(parsed.hostname)
        and parsed.path in ("", "/")
        and not parsed.query and not parsed.fragment
        and not parsed.username and not parsed.password
    )
    if not valid_origin or (not production and parsed.hostname not in ("localhost", "127.0.0.1")):
        raise RuntimeError(
            "Production authentication requires an HTTPS frontend origin"
            if production else "Local authentication requires an HTTP loopback frontend origin"
        )
    secret = os.getenv("AUTH_SECRET", "")
    if len(secret) < 48 or secret.startswith("REPLACE_"):
        raise RuntimeError("AUTH_SECRET must be a random secret of at least 48 characters")
    root = Path(__file__).resolve().parents[3]
    mail_dir = (root / os.getenv("AUTH_MAIL_DIR", "scratch/auth-mail")).resolve()
    if not mail_dir.is_relative_to(root / "scratch"):
        raise RuntimeError("Local mail must be stored beneath the workspace scratch directory")
    return AuthSettings(
        secret=secret,
        frontend_url=frontend,
        mail_dir=mail_dir,
        cookie_name="aligned_session" if production else "aligned_dev_session",
        csrf_cookie_name="aligned_csrf" if production else "aligned_dev_csrf",
        secure_cookies=production,
        cookie_samesite="none" if production else "lax",
    )
