"""Explicit migration runner; never imported by FastAPI startup."""

from alembic import context
from sqlalchemy import String
from sqlmodel.sql.sqltypes import AutoString

from backend.app.db.config import DatabaseSettings
from backend.app.db.models import DatabaseModel
from backend.app.db.session import create_database_engine

config = context.config
target_metadata = DatabaseModel.metadata


def render_item(kind, obj, autogen_context):
    # Freeze migrations as plain SQLAlchemy, independent of future SQLModel code.
    if kind == "type" and isinstance(obj, AutoString):
        return repr(String(length=obj.length)).replace("String(", "sa.String(", 1)
    return False


def configure(**kwargs):
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_item=render_item,
        **kwargs,
    )


def run_migrations_online():
    # Tests may provide an already-owned connection to a disposable database.
    connection = config.attributes.get("connection")
    if connection is not None:
        configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_database_engine(DatabaseSettings.from_environment(migration=True))
    try:
        with engine.connect() as connection:
            configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    # Offline SQL generation does not require credentials or a running database.
    configure(dialect_name="postgresql", literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
