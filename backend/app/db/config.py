"""Explicit database settings; no connection or environment-file loading on import."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url


class DatabaseConfigurationError(RuntimeError):
    pass


_SUPABASE_CA = Path(__file__).resolve().parents[2] / "certs" / "supabase-prod-ca-2021.crt"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_local_environment(path: Path | None = None) -> bool:
    """Explicitly load local settings without overriding process configuration."""

    hosted = bool(os.getenv("RENDER") or os.getenv("VERCEL"))
    production = os.getenv("APP_ENV", "").strip().lower() == "production"
    if hosted or production:
        return False
    return load_dotenv(path or _PROJECT_ROOT / ".env", override=False)


def database_url(value: str, *, production: bool) -> URL:
    try:
        url = make_url(value)
    except Exception:
        raise DatabaseConfigurationError("Database URL is not a valid PostgreSQL URL") from None
    if url.drivername not in ("postgresql", "postgresql+psycopg"):
        raise DatabaseConfigurationError("Use postgresql+psycopg for the database driver")
    if not url.host or not url.database or not url.username:
        raise DatabaseConfigurationError("Database URL must include host, database and username")
    if production and url.query.get("sslmode") != "verify-full":
        raise DatabaseConfigurationError("Production database connections require sslmode=verify-full")
    if production and "sslrootcert" not in url.query:
        # Supabase's pooler certificate chains to its published CA rather than
        # Render's system trust store. Keep verify-full and use the bundled,
        # public Supabase CA for Supabase hosts; other providers use system CAs.
        root_cert = str(_SUPABASE_CA) if url.host.endswith("supabase.com") else "system"
        url = url.update_query_dict({"sslrootcert": root_cert})
    return url.set(drivername="postgresql+psycopg")


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise DatabaseConfigurationError(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise DatabaseConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class DatabaseSettings:
    url: URL = field(repr=False)
    connect_timeout: int = 5
    statement_timeout_ms: int = 15000
    lock_timeout_ms: int = 5000
    pool_size: int = 0
    pool_timeout: int = 5

    @classmethod
    def from_environment(cls, *, migration: bool = False) -> "DatabaseSettings":
        raw = os.getenv("MIGRATION_DATABASE_URL") if migration else None
        raw = raw or os.getenv("DATABASE_URL")
        if not raw:
            raise DatabaseConfigurationError(
                "Set MIGRATION_DATABASE_URL or DATABASE_URL" if migration else "Set DATABASE_URL"
            )
        production = bool(os.getenv("VERCEL")) or os.getenv("APP_ENV", "").lower() == "production"
        return cls(
            url=database_url(raw, production=production),
            connect_timeout=_integer("DB_CONNECT_TIMEOUT_SECONDS", 5, 1, 60),
            statement_timeout_ms=_integer("DB_STATEMENT_TIMEOUT_MS", 15000, 1, 300000),
            lock_timeout_ms=_integer("DB_LOCK_TIMEOUT_MS", 5000, 1, 60000),
            pool_size=0 if migration else _integer("DB_POOL_SIZE", 0, 0, 20),
            pool_timeout=_integer("DB_POOL_TIMEOUT_SECONDS", 5, 1, 60),
        )
