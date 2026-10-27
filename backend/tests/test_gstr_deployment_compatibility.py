"""PostgreSQL rollout matrix for the disabled-by-default GSTR feature."""

from __future__ import annotations

import asyncio
import threading

import pytest
from sqlalchemy import text

from backend.app import main
from backend.app.db.revision import (
    DatabaseRevisionError,
    GSTR_DATABASE_REVISION,
    LEGACY_WEB_DATABASE_REVISION,
    TALLY_EXPORT_DATABASE_REVISION,
)
from backend.app.services import gstr_merge_worker_service as worker_service
from backend.tests.test_database_schema import migrate, schema_engine


pytestmark = pytest.mark.postgres


def _migrate(engine, action: str, target: str) -> None:
    with engine.connect() as connection:
        migrate(connection, action, target)


async def _start_and_stop_web() -> None:
    async with main.lifespan(main.app):
        pass


def test_web_worker_and_non_gstr_rolling_revision_matrix(
    schema_engine, monkeypatch
):
    monkeypatch.setattr(main, "get_database_engine", lambda: schema_engine)
    monkeypatch.setattr(main, "auth_enabled", lambda: True)
    monkeypatch.setattr(main, "auth_settings", lambda: object())
    monkeypatch.setattr(main, "validate_object_storage_configuration", lambda: None)
    monkeypatch.setattr(main, "cleanup_expired_sessions", lambda: None)

    try:
        _migrate(schema_engine, "downgrade", LEGACY_WEB_DATABASE_REVISION)

        monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "false")
        with pytest.raises(DatabaseRevisionError, match=TALLY_EXPORT_DATABASE_REVISION):
            main.validate_application_database_revision()
        with pytest.raises(DatabaseRevisionError, match=TALLY_EXPORT_DATABASE_REVISION):
            asyncio.run(_start_and_stop_web())

        monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "true")
        with pytest.raises(DatabaseRevisionError, match=TALLY_EXPORT_DATABASE_REVISION):
            main.validate_application_database_revision()
        with pytest.raises(DatabaseRevisionError, match=TALLY_EXPORT_DATABASE_REVISION):
            asyncio.run(_start_and_stop_web())
        monkeypatch.setattr(
            worker_service,
            "recover_expired_runs",
            lambda **_kwargs: pytest.fail("revision-11 worker attempted recovery"),
        )
        monkeypatch.setattr(
            worker_service,
            "claim_next_run",
            lambda **_kwargs: pytest.fail("revision-11 worker attempted a claim"),
        )
        with pytest.raises(DatabaseRevisionError, match=GSTR_DATABASE_REVISION):
            worker_service.run_worker_loop(threading.Event(), engine=schema_engine)

        _migrate(schema_engine, "upgrade", GSTR_DATABASE_REVISION)
        for enabled in ("false", "true"):
            monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", enabled)
            with pytest.raises(DatabaseRevisionError, match=TALLY_EXPORT_DATABASE_REVISION):
                main.validate_application_database_revision()
            with pytest.raises(DatabaseRevisionError, match=TALLY_EXPORT_DATABASE_REVISION):
                asyncio.run(_start_and_stop_web())

        stopped = threading.Event()
        worker_calls = []

        def recover_at_head(**_kwargs):
            worker_calls.append("recover")

        def poll_at_head(**_kwargs):
            worker_calls.append("claim")
            stopped.set()
            return None

        monkeypatch.setattr(worker_service, "recover_expired_runs", recover_at_head)
        monkeypatch.setattr(worker_service, "claim_next_run", poll_at_head)
        worker_service.run_worker_loop(stopped, engine=schema_engine)
        assert worker_calls == ["recover", "claim"]

        _migrate(schema_engine, "upgrade", TALLY_EXPORT_DATABASE_REVISION)
        for enabled in ("false", "true"):
            monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", enabled)
            assert main.validate_application_database_revision() == TALLY_EXPORT_DATABASE_REVISION
            asyncio.run(_start_and_stop_web())

        with schema_engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = '20260913_99'")
            )
        monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "false")
        with pytest.raises(DatabaseRevisionError, match=TALLY_EXPORT_DATABASE_REVISION):
            main.validate_application_database_revision()
        with pytest.raises(DatabaseRevisionError, match=GSTR_DATABASE_REVISION):
            worker_service.run_worker_loop(threading.Event(), engine=schema_engine)
    finally:
        with schema_engine.begin() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision == "20260913_99":
                connection.execute(
                    text("UPDATE alembic_version SET version_num = :revision"),
                    {"revision": TALLY_EXPORT_DATABASE_REVISION},
                )
        with schema_engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision in {LEGACY_WEB_DATABASE_REVISION, GSTR_DATABASE_REVISION, "20260923_13"}:
            _migrate(schema_engine, "upgrade", TALLY_EXPORT_DATABASE_REVISION)
