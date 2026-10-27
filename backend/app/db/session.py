"""Short-lived SQL sessions; deliberately not wired into existing routes yet."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session

from .config import DatabaseSettings


def create_database_engine(settings: DatabaseSettings) -> Engine:
    options = {
        "connect_args": {
            "connect_timeout": settings.connect_timeout,
            # Compatible with Supabase's transaction pooler (port 6543).
            "prepare_threshold": None,
            "options": (
                f"-c statement_timeout={settings.statement_timeout_ms} "
                f"-c lock_timeout={settings.lock_timeout_ms}"
            ),
        },
        "hide_parameters": True,
        "echo": False,
        "pool_pre_ping": True,
    }
    if settings.pool_size:
        options.update(pool_size=settings.pool_size, max_overflow=0, pool_timeout=settings.pool_timeout)
    else:
        options["poolclass"] = NullPool
    return create_engine(settings.url, **options)


@lru_cache(maxsize=1)
def get_database_engine() -> Engine:
    return create_database_engine(DatabaseSettings.from_environment())


def get_db_session() -> Generator[Session, None, None]:
    """Future FastAPI dependency. Services explicitly commit their unit of work."""
    with Session(get_database_engine(), expire_on_commit=False) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


def dispose_database_engine() -> None:
    if get_database_engine.cache_info().currsize:
        get_database_engine().dispose()
    get_database_engine.cache_clear()
