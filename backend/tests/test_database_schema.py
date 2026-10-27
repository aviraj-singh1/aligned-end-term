"""Real PostgreSQL migration/constraint tests, never emulated with SQLite.

Uses an EMPTY dedicated database ending in _test. The fixture removes ONLY the
tables/functions created by this migration through its downgrade, after testing.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import os

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, select

from backend.app.db.config import DatabaseSettings, database_url
from backend.app.db.session import create_database_engine
from backend.app.db.models import (
    AuditEvent, AuthIdentity, Client, ClientAssignment, ClientGSTIN, DatabaseModel,
    ReconciliationRun, User, Workspace, WorkspaceInvite, WorkspaceMembership,
)
from backend.app.db.models import OwnershipTransfer
from backend.app.main import REQUIRED_DATABASE_REVISION

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def migrate(connection, action, target):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    getattr(command, action)(config, target)


@pytest.fixture(scope="module")
def schema_engine():
    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL not verified: set TEST_DATABASE_URL and use --require-postgres")
    url = database_url(raw, production=False)
    if not url.database.endswith("_test"):
        pytest.fail("Refusing migration tests: database name must end in _test")
    engine = create_database_engine(DatabaseSettings(url=url))
    try:
        with engine.connect() as connection:
            if inspect(connection).get_table_names(schema="public"):
                pytest.fail("Refusing migration tests: public schema must be empty")
            if connection.scalar(text("SHOW search_path")) not in ('"$user", public', 'public'):
                pytest.fail("Refusing migration tests: use a default public search_path")
            if connection.scalar(text("SELECT current_schema()")) != "public":
                pytest.fail("Refusing migration tests: current schema must be public")
            if connection.scalar(text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'")):
                pytest.fail("Refusing migration tests: public schema must have no pre-existing functions")
            connection.rollback()
            migrate(connection, "upgrade", "head")
        try:
            yield engine
        finally:
            with engine.connect() as connection:
                migrate(connection, "downgrade", "base")
                # Alembic keeps its own empty version table after downgrade.
                connection.execute(text("DROP TABLE alembic_version"))
                connection.commit()
    finally:
        engine.dispose()


@pytest.fixture
def db(schema_engine):
    with schema_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, expire_on_commit=False) as session:
            yield session
        transaction.rollback()


@pytest.fixture
def graph(db):
    user = User(name="Example accountant", email="accountant@example.test", email_normalized="accountant@example.test")
    db.add(user)
    db.flush()
    spaces = [Workspace(name=f"Workspace {n}", type=kind, created_by=user.id)
              for n, kind in enumerate(("INDIVIDUAL", "FIRM", "FIRM"))]
    db.add_all(spaces)
    db.flush()
    members = [WorkspaceMembership(workspace_id=w.id, user_id=user.id, role="ADMIN") for w in spaces]
    clients = [Client(workspace_id=w.id, name="Example client", created_by=user.id) for w in spaces]
    db.add_all(members + clients)
    db.flush()
    gstins = [ClientGSTIN(workspace_id=w.id, client_id=c.id, gstin="06ABCDE1234F1Z5")
              for w, c in zip(spaces, clients)]
    db.add_all(gstins)
    db.flush()
    runs = [ReconciliationRun(workspace_id=w.id, client_id=c.id, client_gstin_id=g.id, created_by=user.id)
            for w, c, g in zip(spaces, clients, gstins)]
    db.add_all(runs)
    db.flush()
    return user, spaces, members, clients, gstins, runs


def rejects(db, row, constraint):
    with pytest.raises(IntegrityError) as exc:
        with db.begin_nested():
            db.add(row)
            db.flush()
    assert exc.value.orig.diag.constraint_name == constraint


def test_round_trip_both_workspace_types_and_same_gstin_in_separate_tenants(db, graph):
    user, spaces, members, clients, gstins, runs = graph
    db.expire_all()
    assert [db.get(Workspace, w.id).type for w in spaces] == ["INDIVIDUAL", "FIRM", "FIRM"]
    assert len(db.exec(select(ClientGSTIN)).all()) == 3
    assert user.created_at.tzinfo is not None
    assert len({m.user_id for m in members}) == 1
    assert all(r.result_version == 0 and not r.artifacts_available for r in runs)


def test_one_active_owner_constraint_and_transfer_token_constraints(db, graph):
    user, spaces, members, *_ = graph
    members[1].role='OWNER';db.add(members[1]);db.flush()
    other=User(name='Other',email='other-owner@example.test',email_normalized='other-owner@example.test')
    db.add(other);db.flush()
    rejects(db,WorkspaceMembership(workspace_id=spaces[1].id,user_id=other.id,role='OWNER'),'uq_workspace_memberships_owner')
    rejects(db,WorkspaceMembership(workspace_id=spaces[2].id,user_id=other.id,role='OWNER',status='SUSPENDED'),'ck_workspace_memberships_owner_active')
    transfer=OwnershipTransfer(workspace_id=spaces[1].id,owner_user_id=user.id,recipient_user_id=other.id,
        owner_email=user.email,recipient_email=other.email,owner_token_hash='a'*64,expires_at=datetime.now(timezone.utc)+timedelta(days=1))
    db.add(transfer);db.flush()
    rejects(db,OwnershipTransfer(workspace_id=spaces[1].id,owner_user_id=user.id,recipient_user_id=other.id,
        owner_email=user.email,recipient_email=other.email,owner_token_hash='b'*64,expires_at=datetime.now(timezone.utc)+timedelta(days=1)),'uq_ownership_transfers_pending')


@pytest.mark.parametrize("source,target", [(0, 1), (1, 0), (1, 2), (2, 1)])
@pytest.mark.parametrize("kind", ["assignment_client", "assignment_member", "gstin", "run_client", "run_gstin", "audit_run"])
def test_cross_workspace_relationships_rejected(db, graph, source, target, kind):
    user, spaces, members, clients, gstins, runs = graph
    workspace = spaces[source]
    if kind == "assignment_client":
        row = ClientAssignment(workspace_id=workspace.id, client_id=clients[target].id, membership_id=members[source].id, assigned_by=user.id)
        constraint = "fk_client_assignments_workspace_id_client_id_clients"
    elif kind == "assignment_member":
        row = ClientAssignment(workspace_id=workspace.id, client_id=clients[source].id, membership_id=members[target].id, assigned_by=user.id)
        constraint = "fk_client_assignments_workspace_id_membership_id_workspace_memberships"
    elif kind == "gstin":
        row = ClientGSTIN(workspace_id=workspace.id, client_id=clients[target].id, gstin="07ABCDE1234F1Z5")
        constraint = "fk_client_gstins_workspace_id_client_id_clients"
    elif kind == "run_client":
        row = ReconciliationRun(workspace_id=workspace.id, client_id=clients[target].id, created_by=user.id)
        constraint = "fk_reconciliation_runs_workspace_id_client_id_clients"
    elif kind == "run_gstin":
        row = ReconciliationRun(workspace_id=workspace.id, client_id=clients[source].id, client_gstin_id=gstins[target].id, created_by=user.id)
        constraint = "fk_reconciliation_runs_workspace_id_client_id_client_gstin_id_client_gstins"
    else:
        row = AuditEvent(workspace_id=workspace.id, client_id=clients[source].id, reconciliation_run_id=runs[target].id, action="TEST", entity_type="run")
        constraint = "fk_audit_events_workspace_id_client_id_reconciliation_run_id_reconciliation_runs"
    # PostgreSQL truncates generated constraint names to its identifier limit.
    from sqlalchemy.dialects.postgresql import dialect
    from sqlalchemy.sql.elements import conv
    constraint = dialect().identifier_preparer.truncate_and_render_constraint_name(conv(constraint), _alembic_quote=False)
    rejects(db, row, constraint)


def test_registration_must_belong_to_same_client_not_just_workspace(db, graph):
    user, spaces, _, _, gstins, _ = graph
    other = Client(workspace_id=spaces[1].id, name="Other", created_by=user.id)
    db.add(other)
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(ReconciliationRun(workspace_id=spaces[1].id, client_id=other.id, client_gstin_id=gstins[1].id, created_by=user.id))
            db.flush()


def test_email_identity_membership_and_gstin_uniqueness(db, graph):
    user, spaces, members, clients, _, _ = graph
    rejects(db, User(name="Duplicate", email=" ACCOUNTANT@EXAMPLE.TEST ", email_normalized=user.email_normalized), "uq_users_email_normalized")
    rejects(db, WorkspaceMembership(workspace_id=spaces[1].id, user_id=user.id, role="ACCOUNTANT"), "uq_workspace_memberships_workspace_id_user_id")
    rejects(db, ClientGSTIN(workspace_id=spaces[1].id, client_id=clients[1].id, gstin="06ABCDE1234F1Z5"), "uq_client_gstins_active_workspace_gstin")
    db.add(AuthIdentity(user_id=user.id, issuer="https://issuer.example", subject="identity-1"))
    db.flush()
    rejects(db, AuthIdentity(user_id=user.id, issuer="https://issuer.example", subject="identity-1"), "uq_auth_identities_issuer_subject")


def test_invites_allow_reissue_only_after_pending_invite_closed(db, graph):
    user, spaces, *_ = graph
    def invite():
        return WorkspaceInvite(workspace_id=spaces[1].id, email_normalized="invite@example.test", role="ACCOUNTANT", token_hash=uuid4().hex + uuid4().hex, invited_by=user.id, expires_at=datetime.now(timezone.utc)+timedelta(days=1))
    first = invite()
    db.add(first)
    db.flush()
    rejects(db, invite(), "uq_workspace_invites_pending_email")
    first.status = "REVOKED"
    first.revoked_at = datetime.now(timezone.utc)
    db.flush()
    db.add(invite())
    db.flush()


@pytest.mark.parametrize("field,value,constraint", [
    ("token_hash", "raw-token", "ck_workspace_invites_token_digest"),
    ("email_normalized", "UPPER@EXAMPLE.TEST", "ck_workspace_invites_email_normalized"),
    ("status", "ACCEPTED", "ck_workspace_invites_accepted_state"),
    ("expires_at", datetime(2000, 1, 1, tzinfo=timezone.utc), "ck_workspace_invites_expiry"),
])
def test_invite_invalid_state(db, graph, field, value, constraint):
    user, spaces, *_ = graph
    row = WorkspaceInvite(workspace_id=spaces[1].id, email_normalized="invite@example.test", role="ACCOUNTANT", token_hash="a"*64, invited_by=user.id, expires_at=datetime.now(timezone.utc)+timedelta(days=1))
    setattr(row, field, value)
    rejects(db, row, constraint)


def test_audit_optional_run_cannot_bypass_client_scope(db, graph):
    _, spaces, _, _, _, runs = graph
    rejects(db, AuditEvent(workspace_id=spaces[1].id, reconciliation_run_id=runs[1].id, action="TEST", entity_type="run"), "ck_audit_events_run_requires_client")


@pytest.mark.parametrize("mutation", ["UPDATE audit_events SET action = 'CHANGED'", "DELETE FROM audit_events", "TRUNCATE audit_events"])
def test_audit_history_is_append_only(db, graph, mutation):
    user, spaces, *_ = graph
    event = AuditEvent(workspace_id=spaces[1].id, actor_user_id=user.id, action="TEST", entity_type="workspace")
    db.add(event)
    db.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin_nested():
            db.execute(text(mutation))
    assert db.get(AuditEvent, event.id).action == "TEST"


def test_audit_dedup_and_bounded_json(db, graph):
    _, spaces, *_ = graph
    operation = uuid4()
    values = dict(workspace_id=spaces[1].id, action="TEST", entity_type="workspace", operation_id=operation)
    db.add(AuditEvent(**values))
    db.flush()
    rejects(db, AuditEvent(**values), "uq_audit_events_workspace_id_operation_id_action")
    rejects(db, AuditEvent(workspace_id=spaces[1].id, action="BIG", entity_type="workspace", event_metadata={"payload": "x"*17000}), "ck_audit_events_metadata_bounded_object")


def test_runs_validate_versions_summary_and_completion(db, graph):
    user, spaces, _, clients, _, _ = graph
    values = dict(workspace_id=spaces[1].id, client_id=clients[1].id, created_by=user.id)
    rejects(db, ReconciliationRun(**values, input_version=0), "ck_reconciliation_runs_versions")
    rejects(db, ReconciliationRun(**values, status="COMPLETED"), "ck_reconciliation_runs_completed_state")
    rejects(db, ReconciliationRun(**values, summary_json={"big": "x"*66000}), "ck_reconciliation_runs_summary_bounded_object")
    session_id = uuid4()
    db.add(ReconciliationRun(**values, redis_session_id=session_id))
    db.flush()
    rejects(db, ReconciliationRun(**values, redis_session_id=session_id), "uq_reconciliation_runs_redis_session_id")


def test_invalid_workspace_type_and_destructive_parent_delete_rejected(db, graph):
    user, spaces, *_ = graph
    rejects(db, Workspace(name="Bad", type="SUBSIDIARY", created_by=user.id), "ck_workspaces_type")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.delete(spaces[1])
            db.flush()


@pytest.mark.parametrize("exception", [False, True])
def test_request_session_does_not_commit_unfinished_work(schema_engine, monkeypatch, exception):
    from backend.app.db import session as session_module
    monkeypatch.setattr(session_module, "get_database_engine", lambda: schema_engine)
    dependency = session_module.get_db_session()
    request_session = next(dependency)
    user = User(name="Rolled back", email="rollback@example.test", email_normalized="rollback@example.test")
    request_session.add(user)
    request_session.flush()
    if exception:
        with pytest.raises(RuntimeError, match="request failed"):
            dependency.throw(RuntimeError("request failed"))
    else:
        dependency.close()
    with Session(schema_engine) as check:
        assert check.get(User, user.id) is None


def test_schema_matches_models_and_migration_round_trip(schema_engine):
    with schema_engine.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) == set(DatabaseModel.metadata.tables) | {"alembic_version"}
        connection.rollback()
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.check(config)
        migrate(connection, "downgrade", "base")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        connection.rollback()
        migrate(connection, "upgrade", "head")
        command.check(config)


def test_runtime_required_revision_matches_migration_head(schema_engine):
    with schema_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REQUIRED_DATABASE_REVISION
