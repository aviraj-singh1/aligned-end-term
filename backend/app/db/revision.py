"""Bounded database-revision checks for web and GSTR worker processes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

LEGACY_WEB_DATABASE_REVISION = "20260910_11"
GSTR_DATABASE_REVISION = "20260914_12"
TALLY_EXPORT_DATABASE_REVISION = "20260923_14"
GSTR_COMPATIBLE_DATABASE_REVISIONS = frozenset(
    {GSTR_DATABASE_REVISION, "20260923_13", TALLY_EXPORT_DATABASE_REVISION}
)
KNOWN_DATABASE_REVISIONS = frozenset(
    {LEGACY_WEB_DATABASE_REVISION, *GSTR_COMPATIBLE_DATABASE_REVISIONS}
)
WEB_COMPATIBLE_DATABASE_REVISIONS = frozenset({TALLY_EXPORT_DATABASE_REVISION})


class DatabaseRevisionError(RuntimeError):
    """Raised when a process cannot safely use the connected schema."""


def current_database_revision(bind: Any) -> str:
    """Return the single Alembic revision recorded by the database."""

    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return current_database_revision(connection)

    try:
        revisions = list(
            bind.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )
    except SQLAlchemyError:
        raise DatabaseRevisionError(
            "The database revision could not be verified."
        ) from None
    if len(revisions) != 1 or not revisions[0]:
        raise DatabaseRevisionError(
            "The database must have exactly one supported Alembic revision."
        )
    return str(revisions[0])


def validate_web_database_revision(
    bind: Any, *, gstr_consolidation_enabled: bool
) -> str:
    """Accept only the explicitly supported rolling-deployment revisions."""

    revision = current_database_revision(bind)
    if revision != TALLY_EXPORT_DATABASE_REVISION:
        raise DatabaseRevisionError(
            f"The public application requires database revision {TALLY_EXPORT_DATABASE_REVISION}."
        )
    return revision


def validate_gstr_database_revision(bind: Any) -> str:
    """Require the exact GSTR schema before any GSTR table access or worker poll."""

    revision = current_database_revision(bind)
    if revision not in GSTR_COMPATIBLE_DATABASE_REVISIONS:
        raise DatabaseRevisionError(
            f"GSTR processing requires database revision {GSTR_DATABASE_REVISION}."
        )
    return revision


def gstr_schema_available(bind: Any) -> bool:
    """Return whether GSTR tables exist, rejecting unknown rolling revisions."""

    revision = current_database_revision(bind)
    if revision not in KNOWN_DATABASE_REVISIONS:
        raise DatabaseRevisionError(
            "The database revision is not supported by this application release."
        )
    return revision in GSTR_COMPATIBLE_DATABASE_REVISIONS
