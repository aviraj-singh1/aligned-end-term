"""Real-PostgreSQL authorization and lifecycle tests for GSTR merge runs."""

import csv
import io
import zipfile
from dataclasses import dataclass
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import (
    AuditEvent,
    Client,
    ClientAssignment,
    ClientGSTIN,
    GstrMergeRun,
    User,
    Workspace,
    WorkspaceMembership,
)
from backend.app.services import data_management_service
from backend.app.services import gstr_merge_run_service as service
from backend.tests.test_database_schema import schema_engine


pytestmark = pytest.mark.postgres


@dataclass
class Graph:
    workspace: Workspace
    owner: User
    admin: User
    assigned: User
    unassigned: User
    suspended: User
    removed: User
    client: Client
    other_client: Client
    archived_client: Client
    gstin: ClientGSTIN
    other_gstin: ClientGSTIN
    inactive_gstin: ClientGSTIN
    other_workspace: Workspace
    outsider: User
    outsider_client: Client
    outsider_gstin: ClientGSTIN


@pytest.fixture
def graph(schema_engine, monkeypatch):
    monkeypatch.setattr(service, "get_database_engine", lambda: schema_engine)
    monkeypatch.setattr(data_management_service, "get_database_engine", lambda: schema_engine)

    def person(db, label):
        user = User(
            name=f"{label} user",
            email=f"{label}-{uuid4()}@example.test",
            email_normalized="placeholder@example.test",
            email_verified_at=utcnow(),
        )
        user.email_normalized = user.email
        db.add(user)
        db.flush()
        return user

    with Session(schema_engine, expire_on_commit=False) as db:
        owner = person(db, "owner")
        workspace = Workspace(name=f"GSTR workspace {uuid4()}", type="FIRM", created_by=owner.id)
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMembership(
            workspace_id=workspace.id, user_id=owner.id, role="OWNER", joined_at=utcnow()
        ))

        users = {name: person(db, name) for name in (
            "admin", "assigned", "unassigned", "suspended", "removed"
        )}
        memberships = {}
        for name, role, status in (
            ("admin", "ADMIN", "ACTIVE"),
            ("assigned", "ACCOUNTANT", "ACTIVE"),
            ("unassigned", "ACCOUNTANT", "ACTIVE"),
            ("suspended", "ACCOUNTANT", "SUSPENDED"),
            ("removed", "ACCOUNTANT", "REMOVED"),
        ):
            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=users[name].id,
                role=role,
                status=status,
                joined_at=utcnow(),
            )
            db.add(membership)
            db.flush()
            memberships[name] = membership

        client = Client(workspace_id=workspace.id, name="Active synthetic client", created_by=owner.id)
        other_client = Client(workspace_id=workspace.id, name="Other synthetic client", created_by=owner.id)
        archived_client = Client(
            workspace_id=workspace.id,
            name="Archived synthetic client",
            created_by=owner.id,
            status="ARCHIVED",
        )
        db.add_all([client, other_client, archived_client])
        db.flush()
        gstin = ClientGSTIN(
            workspace_id=workspace.id, client_id=client.id, gstin="06AAAAA0000A1Z5"
        )
        inactive_gstin = ClientGSTIN(
            workspace_id=workspace.id,
            client_id=client.id,
            gstin="07AAAAA0000A1Z4",
            status="INACTIVE",
        )
        other_gstin = ClientGSTIN(
            workspace_id=workspace.id, client_id=other_client.id, gstin="08AAAAA0000A1Z3"
        )
        db.add_all([gstin, inactive_gstin, other_gstin])
        db.flush()
        db.add(ClientAssignment(
            workspace_id=workspace.id,
            client_id=client.id,
            membership_id=memberships["assigned"].id,
            assigned_by=owner.id,
        ))

        outsider = person(db, "outsider")
        other_workspace = Workspace(name=f"Other workspace {uuid4()}", type="FIRM", created_by=outsider.id)
        db.add(other_workspace)
        db.flush()
        db.add(WorkspaceMembership(
            workspace_id=other_workspace.id, user_id=outsider.id, role="OWNER", joined_at=utcnow()
        ))
        outsider_client = Client(
            workspace_id=other_workspace.id, name="Outside client", created_by=outsider.id
        )
        db.add(outsider_client)
        db.flush()
        outsider_gstin = ClientGSTIN(
            workspace_id=other_workspace.id,
            client_id=outsider_client.id,
            gstin="09AAAAA0000A1Z2",
        )
        db.add(outsider_gstin)
        db.commit()

    return Graph(
        workspace=workspace,
        owner=owner,
        admin=users["admin"],
        assigned=users["assigned"],
        unassigned=users["unassigned"],
        suspended=users["suspended"],
        removed=users["removed"],
        client=client,
        other_client=other_client,
        archived_client=archived_client,
        gstin=gstin,
        other_gstin=other_gstin,
        inactive_gstin=inactive_gstin,
        other_workspace=other_workspace,
        outsider=outsider,
        outsider_client=outsider_client,
        outsider_gstin=outsider_gstin,
    )


def create(graph, actor=None, **overrides):
    return service.create_run(
        actor or graph.owner,
        graph.workspace.id,
        overrides.pop("client_id", graph.client.id),
        overrides.pop("client_gstin_id", graph.gstin.id),
        overrides.pop("mode", "QUARTERLY"),
        overrides.pop("financial_year", "2025-26"),
    )


@pytest.mark.parametrize("actor_name", ["owner", "admin", "assigned"])
def test_authorized_workspace_roles_can_create(graph, schema_engine, actor_name):
    result = create(graph, getattr(graph, actor_name))
    assert result["status"] == "UPLOADING"
    assert result["version"] == 1
    with Session(schema_engine) as db:
        event = db.exec(select(AuditEvent).where(
            AuditEvent.entity_id == result["id"], AuditEvent.action == "GSTR_MERGE_CREATED"
        )).one()
        assert event.client_id == graph.client.id
        assert event.event_metadata == {"mode": "QUARTERLY", "financial_year": "2025-26"}


def test_unassigned_accountant_is_denied(graph):
    with pytest.raises(HTTPException) as exc:
        create(graph, graph.unassigned)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("actor_name", ["suspended", "removed"])
def test_inactive_membership_is_denied(graph, actor_name):
    with pytest.raises(HTTPException) as exc:
        create(graph, getattr(graph, actor_name))
    assert exc.value.status_code == 404


def test_cross_workspace_client_is_hidden(graph):
    with pytest.raises(HTTPException) as exc:
        service.create_run(
            graph.owner,
            graph.workspace.id,
            graph.outsider_client.id,
            graph.outsider_gstin.id,
            "QUARTERLY",
            "2025-26",
        )
    assert exc.value.status_code == 404


@pytest.mark.parametrize("gstin_name", ["other_gstin", "inactive_gstin"])
def test_wrong_or_inactive_gstin_is_hidden(graph, gstin_name):
    with pytest.raises(HTTPException) as exc:
        create(graph, client_gstin_id=getattr(graph, gstin_name).id)
    assert exc.value.status_code == 404


def test_inactive_client_is_hidden_even_from_owner(graph):
    with pytest.raises(HTTPException) as exc:
        create(graph, client_id=graph.archived_client.id, client_gstin_id=graph.gstin.id)
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "WEEKLY"},
        {"financial_year": "2025/26"},
        {"financial_year": "2025-27"},
    ],
)
def test_mode_and_financial_year_are_validated(graph, values):
    with pytest.raises(HTTPException) as exc:
        create(graph, **values)
    assert exc.value.status_code == 422


def test_list_and_lookup_are_client_and_tenant_scoped(graph):
    first = create(graph)
    second = service.create_run(
        graph.owner, graph.workspace.id, graph.other_client.id, graph.other_gstin.id,
        "MONTHLY", "2025-26",
    )
    assert [row["id"] for row in service.list_runs(
        graph.owner, graph.workspace.id, graph.client.id
    )] == [first["id"]]
    with pytest.raises(HTTPException) as unassigned:
        service.list_runs(graph.assigned, graph.workspace.id, graph.other_client.id)
    assert unassigned.value.status_code == 404
    with pytest.raises(HTTPException) as cross_tenant:
        service.get_run(
            graph.outsider, graph.other_workspace.id, graph.outsider_client.id, first["id"]
        )
    assert cross_tenant.value.status_code == 404
    assert service.get_run(
        graph.owner, graph.workspace.id, graph.other_client.id, second["id"]
    )["id"] == second["id"]


def test_state_machine_and_optimistic_version(graph):
    run = create(graph)
    queued = service.transition_run(
        graph.owner, graph.workspace.id, graph.client.id, run["id"], "QUEUED", 1
    )
    with pytest.raises(HTTPException) as stale:
        service.transition_run(
            graph.owner, graph.workspace.id, graph.client.id, run["id"], "PROCESSING", 1
        )
    assert stale.value.status_code == 409
    processing = service.transition_run(
        graph.owner, graph.workspace.id, graph.client.id, run["id"], "PROCESSING", queued["version"]
    )
    assert processing["attempt_count"] == 1 and processing["started_at"] is not None
    completed = service.transition_run(
        graph.owner,
        graph.workspace.id,
        graph.client.id,
        run["id"],
        "COMPLETED",
        processing["version"],
        result_summary={"categories": {"B2B": 4}, "warning_count": 0},
        output_object_ref="opaque://result",
        output_size_bytes=1024,
        output_sha256="a" * 64,
    )
    assert completed["status"] == "COMPLETED" and completed["completed_at"] is not None
    with pytest.raises(HTTPException) as invalid:
        service.transition_run(
            graph.owner, graph.workspace.id, graph.client.id, run["id"],
            "PROCESSING", completed["version"],
        )
    assert invalid.value.status_code == 409


def test_failed_retry_and_expiry_transitions(graph):
    run = create(graph)
    failed = service.transition_run(
        graph.owner, graph.workspace.id, graph.client.id, run["id"], "FAILED", 1,
        error_code="INVALID_WORKBOOK", safe_error_message="Workbook validation failed",
    )
    assert failed["failed_at"] is not None
    queued = service.transition_run(
        graph.owner, graph.workspace.id, graph.client.id, run["id"], "QUEUED", failed["version"]
    )
    expired = service.expire_run(
        graph.owner, graph.workspace.id, graph.client.id, run["id"], queued["version"]
    )
    assert expired["status"] == "EXPIRED" and expired["expires_at"] is not None


def test_soft_delete_is_audited_and_hidden(graph, schema_engine):
    run = create(graph)
    deleted = service.soft_delete_run(
        graph.owner, graph.workspace.id, graph.client.id, run["id"], run["version"]
    )
    assert deleted["status"] == "DELETED" and deleted["deleted_at"] is not None
    with pytest.raises(HTTPException) as hidden:
        service.get_run(graph.owner, graph.workspace.id, graph.client.id, run["id"])
    assert hidden.value.status_code == 404
    with Session(schema_engine) as db:
        assert db.exec(select(AuditEvent).where(
            AuditEvent.entity_id == run["id"], AuditEvent.action == "GSTR_MERGE_DELETED"
        )).one()


def test_metadata_is_bounded_and_rejects_financial_rows(graph):
    run = create(graph)
    with pytest.raises(HTTPException):
        service.set_input_manifest(
            graph.owner, graph.workspace.id, graph.client.id, run["id"],
            {"rows": [{"invoice": "not allowed"}]}, run["version"],
        )
    with pytest.raises(HTTPException):
        service.set_input_manifest(
            graph.owner, graph.workspace.id, graph.client.id, run["id"],
            {"metadata": "x" * 66_000}, run["version"],
        )


def test_workspace_export_is_metadata_only_and_reset_retires_runs(graph, schema_engine):
    run = create(graph)
    secret_ref = f"opaque-secret-{uuid4()}"
    with Session(schema_engine) as db:
        row = db.get(GstrMergeRun, run["id"])
        row.input_manifest_json = {"files": [{"object_ref": secret_ref}]}
        row.result_summary_json = {"categories": {"B2B": 4}}
        db.add(row)
        db.commit()

    exported = data_management_service.export_workspace(graph.owner, graph.workspace.id)
    with zipfile.ZipFile(io.BytesIO(exported.body)) as archive:
        assert "gstr_merge_runs.csv" in archive.namelist()
        rows = list(csv.DictReader(io.StringIO(
            archive.read("gstr_merge_runs.csv").decode("utf-8-sig")
        )))
        assert any(row["id"] == str(run["id"]) for row in rows)
        assert secret_ref.encode() not in exported.body
        assert b"input_manifest_json" not in archive.read("gstr_merge_runs.csv")
        assert b"result_summary_json" not in archive.read("gstr_merge_runs.csv")

    data_management_service.reset_workspace(
        graph.owner, graph.workspace.id, graph.workspace.name
    )
    with Session(schema_engine) as db:
        retired = db.get(GstrMergeRun, run["id"])
        assert retired.status == "DELETED"
        assert retired.deleted_at is not None
        event = db.exec(select(AuditEvent).where(
            AuditEvent.workspace_id == graph.workspace.id,
            AuditEvent.action == "WORKSPACE_RESET",
        )).one()
        assert event.event_metadata["gstr_merge_runs"] >= 1


def test_database_constraints_reject_cross_client_and_invalid_metadata(graph, schema_engine):
    with Session(schema_engine) as db:
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(GstrMergeRun(
                    workspace_id=graph.workspace.id,
                    client_id=graph.client.id,
                    client_gstin_id=graph.other_gstin.id,
                    created_by=graph.owner.id,
                    mode="QUARTERLY",
                    financial_year="2025-26",
                ))
                db.flush()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(GstrMergeRun(
                    workspace_id=graph.workspace.id,
                    client_id=graph.client.id,
                    client_gstin_id=graph.gstin.id,
                    created_by=graph.owner.id,
                    mode="WEEKLY",
                    financial_year="2025-26",
                ))
                db.flush()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(GstrMergeRun(
                    workspace_id=graph.workspace.id,
                    client_id=graph.client.id,
                    client_gstin_id=graph.gstin.id,
                    created_by=graph.owner.id,
                    mode="QUARTERLY",
                    financial_year="2025-27",
                ))
                db.flush()
