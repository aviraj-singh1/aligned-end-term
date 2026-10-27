from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy.pool import NullPool, QueuePool

from backend.app.db.config import DatabaseConfigurationError, DatabaseSettings, database_url
from backend.app.db.session import create_database_engine

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def database_environment(monkeypatch):
    for name in (
        "DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_ENV", "VERCEL",
        "DB_POOL_SIZE", "DB_POOL_TIMEOUT_SECONDS", "DB_CONNECT_TIMEOUT_SECONDS",
        "DB_STATEMENT_TIMEOUT_MS", "DB_LOCK_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_no_database_required_until_dependency_is_used():
    from backend.app.main import app
    assert app is not None
    with pytest.raises(DatabaseConfigurationError, match="Set DATABASE_URL"):
        DatabaseSettings.from_environment()


def test_url_selection_does_not_leak_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:do-not-print@localhost/aligned")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://migrator:also-private@localhost/aligned")
    app = DatabaseSettings.from_environment()
    migration = DatabaseSettings.from_environment(migration=True)
    assert app.url.drivername == "postgresql+psycopg"
    assert app.url.username == "app"
    assert migration.url.username == "migrator"
    assert "do-not-print" not in repr(app)
    assert "also-private" not in repr(migration)


@pytest.mark.parametrize("value", [
    "not-a-url:private-secret", "sqlite:///private-secret.db",
    "postgresql+asyncpg://user:private-secret@localhost/db",
    "postgresql://user:private-secret@localhost", "postgresql:///db",
])
def test_invalid_urls_fail_without_echoing_input(value):
    with pytest.raises(DatabaseConfigurationError) as exc:
        database_url(value, production=False)
    assert "private-secret" not in str(exc.value)


@pytest.mark.parametrize("sslmode", ["disable", "prefer", "require", "verify-ca"])
def test_production_requires_hostname_verified_tls(sslmode):
    with pytest.raises(DatabaseConfigurationError, match="verify-full"):
        database_url(f"postgresql://user:secret@db.example/aligned?sslmode={sslmode}", production=True)
    assert database_url("postgresql://user:secret@db.example/aligned?sslmode=verify-full", production=True)


def test_production_uses_system_ca_when_root_certificate_is_not_configured():
    url = database_url(
        "postgresql://user:secret@db.example/aligned?sslmode=verify-full",
        production=True,
    )
    assert url.query["sslrootcert"] == "system"


def test_production_uses_bundled_ca_for_supabase_pooler():
    url = database_url(
        "postgresql://user:secret@aws-0-ap-southeast-2.pooler.supabase.com/postgres?sslmode=verify-full",
        production=True,
    )
    certificate = Path(url.query["sslrootcert"])
    assert certificate.name == "supabase-prod-ca-2021.crt"
    assert certificate.is_file()


def test_production_preserves_explicit_root_certificate():
    url = database_url(
        "postgresql://user:secret@db.example/aligned?sslmode=verify-full&sslrootcert=/etc/secrets/supabase-ca.crt",
        production=True,
    )
    assert url.query["sslrootcert"] == "/etc/secrets/supabase-ca.crt"


@pytest.mark.parametrize("name,value", [
    ("DB_POOL_SIZE", "-1"), ("DB_POOL_SIZE", "21"),
    ("DB_CONNECT_TIMEOUT_SECONDS", "0"), ("DB_LOCK_TIMEOUT_MS", "bad"),
])
def test_invalid_resource_budgets_fail(name, value, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/aligned")
    monkeypatch.setenv(name, value)
    with pytest.raises(DatabaseConfigurationError, match=name):
        DatabaseSettings.from_environment()


@pytest.mark.parametrize("size,kind", [(0, NullPool), (2, QueuePool)])
def test_engine_configuration_is_lazy_and_bounded(size, kind):
    engine = create_database_engine(DatabaseSettings(
        url=database_url("postgresql://user@localhost:1/not_running", production=False), pool_size=size,
    ))
    try:
        assert isinstance(engine.pool, kind)
        assert engine.hide_parameters
        assert engine.echo is False
        if size:
            assert engine.pool.size() == size
    finally:
        engine.dispose()


def test_initial_migration_renders_without_database_credentials():
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE workspaces" in sql
    assert "CREATE TABLE audit_events" in sql
    assert "CREATE TABLE gstr_merge_runs" in sql
    assert "workspace_id, client_id, client_gstin_id" in sql
    assert "ck_gstr_merge_runs_input_manifest_bounded_object" in sql
    assert "ix_gstr_merge_runs_queue" in sql
    assert "reject_audit_mutation" in sql
