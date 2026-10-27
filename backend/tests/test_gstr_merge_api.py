"""PostgreSQL API, authorization, CSRF, and lifecycle tests for Phase 3."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from backend.app.api.routes.gstr_merges import router as gstr_router
from backend.app.auth.security import utcnow
from backend.app.db.models import (
    AuditEvent, Client, ClientAssignment, ClientGSTIN, GstrMergeRun,
    WorkspaceMembership,
)
from backend.app.services import gstr_merge_lifecycle_service as lifecycle
from backend.app.services import object_storage
from backend.tests.test_auth_api import auth_api
from backend.tests.test_database_schema import schema_engine
from backend.tests.test_workspace_api import actor, login


pytestmark = pytest.mark.postgres


@dataclass
class ApiGraph:
    client: object
    engine: object
    owner: tuple
    admin: tuple
    assigned: tuple
    unassigned: tuple
    suspended: tuple
    removed: tuple
    outsider: tuple
    business: Client
    other_business: Client
    archived_business: Client
    gstin: ClientGSTIN
    other_gstin: ClientGSTIN
    inactive_gstin: ClientGSTIN


@pytest.fixture
def gstr_api(auth_api, monkeypatch, tmp_path):
    client, _, engine = auth_api
    monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "true")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(object_storage, "local_storage_root", lambda: tmp_path / "objects")
    monkeypatch.setattr(lifecycle, "get_database_engine", lambda: engine)
    client.app.include_router(gstr_router, prefix="/api")

    owner = actor(engine)
    admin = actor(engine, workspace=owner[1], role="ADMIN")
    assigned = actor(engine, workspace=owner[1], role="ACCOUNTANT")
    unassigned = actor(engine, workspace=owner[1], role="ACCOUNTANT")
    suspended = actor(engine, workspace=owner[1], role="ACCOUNTANT")
    removed = actor(engine, workspace=owner[1], role="ACCOUNTANT")
    outsider = actor(engine)
    with Session(engine, expire_on_commit=False) as db:
        for who, status in ((suspended, "SUSPENDED"), (removed, "REMOVED")):
            membership = db.get(WorkspaceMembership, who[2].id)
            membership.status = status
            db.add(membership)
        business = Client(
            workspace_id=owner[1].id, created_by=owner[0].id, name="GSTR client"
        )
        other_business = Client(
            workspace_id=owner[1].id, created_by=owner[0].id, name="Other client"
        )
        archived_business = Client(
            workspace_id=owner[1].id, created_by=owner[0].id,
            name="Archived client", status="ARCHIVED",
        )
        db.add_all([business, other_business, archived_business])
        db.flush()
        gstin = ClientGSTIN(
            workspace_id=owner[1].id, client_id=business.id,
            gstin="27ABCDE1234F1Z5",
        )
        inactive_gstin = ClientGSTIN(
            workspace_id=owner[1].id, client_id=business.id,
            gstin="28ABCDE1234F1Z4", status="INACTIVE",
        )
        other_gstin = ClientGSTIN(
            workspace_id=owner[1].id, client_id=other_business.id,
            gstin="29ABCDE1234F1Z3",
        )
        db.add_all([gstin, inactive_gstin, other_gstin])
        db.flush()
        db.add(ClientAssignment(
            workspace_id=owner[1].id,
            client_id=business.id,
            membership_id=assigned[2].id,
            assigned_by=owner[0].id,
        ))
        db.commit()
    return ApiGraph(
        client, engine, owner, admin, assigned, unassigned, suspended, removed,
        outsider, business, other_business, archived_business, gstin, other_gstin,
        inactive_gstin,
    )


def quarterly_files(size=11):
    return [
        {"period_code": period, "filename": f"{period}.xlsx", "size_bytes": size}
        for period in lifecycle.QUARTERLY_PERIODS
    ]


def payload(graph, **overrides):
    value = {
        "client_gstin_id": str(graph.gstin.id),
        "financial_year": "2025-26",
        "mode": "QUARTERLY",
        "files": quarterly_files(),
    }
    value.update(overrides)
    return value


def mutation_headers(client, *, key=None, version=None, expected_user=None):
    csrf = client.get("/api/auth/csrf")
    assert csrf.status_code == 200
    headers = {
        "X-CSRF-Token": csrf.json()["csrf_token"],
        "Idempotency-Key": str(key or uuid4()),
    }
    if version is not None:
        headers["X-Run-Version"] = str(version)
    if expected_user is not None:
        headers["X-Expected-User-ID"] = str(expected_user)
    return headers


def root(graph, *, client_id=None):
    return (
        f"/api/workspaces/{graph.owner[1].id}/clients/"
        f"{client_id or graph.business.id}/gstr-merges"
    )


def create(graph, *, who=None, key=None, data=None):
    login(graph.client, who or graph.owner)
    return graph.client.post(
        root(graph), json=data or payload(graph),
        headers=mutation_headers(graph.client, key=key),
    )


def upload_all(graph, created, *, who=None, body=b"PK workbook"):
    login(graph.client, who or graph.owner)
    for target in created["upload_targets"]:
        headers = mutation_headers(graph.client)
        headers.update(target["headers"])
        response = graph.client.put(target["url"], content=body, headers=headers)
        assert response.status_code == 200, response.text


def submit(graph, run, *, who=None, key=None, version=None):
    login(graph.client, who or graph.owner)
    return graph.client.post(
        root(graph) + f"/{run['id']}/submit", json={},
        headers=mutation_headers(
            graph.client, key=key, version=run["version"] if version is None else version
        ),
    )


def stored_run(graph, run_id):
    with Session(graph.engine, expire_on_commit=False) as db:
        return db.get(GstrMergeRun, run_id)


@pytest.mark.parametrize("role", ["owner", "admin", "assigned"])
def test_authorized_roles_can_create_list_get_upload_submit_download_and_delete(gstr_api, role):
    who = getattr(gstr_api, role)
    response = create(gstr_api, who=who)
    assert response.status_code == 201, response.text
    created = response.json()
    run = created["run"]
    assert run["status"] == "UPLOADING" and len(created["upload_targets"]) == 4
    assert "object_ref" not in response.text and "input_manifest" not in response.text

    login(gstr_api.client, who)
    listed = gstr_api.client.get(root(gstr_api))
    assert any(item["id"] == run["id"] for item in listed.json()["items"])
    assert gstr_api.client.get(root(gstr_api) + f"/{run['id']}").status_code == 200
    upload_all(gstr_api, created, who=who)
    queued = submit(gstr_api, run, who=who)
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "QUEUED"
    assert queued.json()["queued_at"] and queued.json()["uploaded_total_size_bytes"] == 44

    second = create(gstr_api, who=who).json()["run"]
    with Session(gstr_api.engine) as db:
        row = db.get(GstrMergeRun, second["id"])
        output_ref = object_storage.gstr_output_reference(
            row.workspace_id, row.client_id, row.id, row.engine_version
        )
        output_path = Path(output_ref)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"PK result")
        row.status = "COMPLETED"
        row.completed_at = utcnow()
        row.expires_at = utcnow() + timedelta(days=1)
        row.output_object_ref = output_ref
        row.output_size_bytes = 9
        row.output_sha256 = "a" * 64
        row.result_summary_json = {"warning_count": 0}
        db.add(row)
        db.commit()
    login(gstr_api.client, who)
    downloaded = gstr_api.client.get(root(gstr_api) + f"/{second['id']}/download")
    assert downloaded.status_code == 200 and downloaded.content == b"PK result"
    with Session(gstr_api.engine) as db:
        assert db.exec(select(AuditEvent).where(
            AuditEvent.entity_id == second["id"],
            AuditEvent.action == "GSTR_MERGE_DOWNLOADED",
        )).one()

    deleted = gstr_api.client.delete(
        root(gstr_api) + f"/{run['id']}",
        headers=mutation_headers(gstr_api.client, version=queued.json()["version"]),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["run"]["status"] == "DELETED"


def test_disabled_feature_blocks_create_and_submit(gstr_api, monkeypatch):
    created = create(gstr_api).json()
    upload_all(gstr_api, created)

    monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "false")
    blocked_create = create(gstr_api)
    assert blocked_create.status_code == 503
    assert blocked_create.json() == {
        "detail": "GSTR Consolidation is temporarily unavailable."
    }

    blocked_submit = submit(gstr_api, created["run"])
    assert blocked_submit.status_code == 503
    assert blocked_submit.json() == blocked_create.json()
    assert stored_run(gstr_api, created["run"]["id"]).status == "UPLOADING"


def test_disabled_feature_preserves_existing_run_access_and_cleanup(gstr_api, monkeypatch):
    completed = create(gstr_api).json()["run"]
    removable = create(gstr_api).json()["run"]
    with Session(gstr_api.engine) as db:
        row = db.get(GstrMergeRun, completed["id"])
        output_ref = object_storage.gstr_output_reference(
            row.workspace_id, row.client_id, row.id, row.engine_version
        )
        output_path = Path(output_ref)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"PK existing result")
        row.status = "COMPLETED"
        row.completed_at = utcnow()
        row.expires_at = utcnow() + timedelta(hours=1)
        row.output_object_ref = output_ref
        row.output_size_bytes = len(b"PK existing result")
        row.output_sha256 = "b" * 64
        row.result_summary_json = {"warning_count": 0}
        db.add(row)
        db.commit()

    monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "false")
    login(gstr_api.client, gstr_api.owner)
    listed = gstr_api.client.get(root(gstr_api))
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} >= {
        completed["id"], removable["id"]
    }
    assert gstr_api.client.get(root(gstr_api) + f"/{completed['id']}").status_code == 200
    downloaded = gstr_api.client.get(
        root(gstr_api) + f"/{completed['id']}/download"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"PK existing result"
    deleted = gstr_api.client.delete(
        root(gstr_api) + f"/{removable['id']}",
        headers=mutation_headers(gstr_api.client, version=removable["version"]),
    )
    assert deleted.status_code == 200
    assert deleted.json()["run"]["status"] == "DELETED"


@pytest.mark.parametrize("role", ["unassigned", "suspended", "removed", "outsider"])
def test_denied_memberships_cannot_create_or_touch_storage(gstr_api, monkeypatch, role):
    calls = []
    monkeypatch.setattr(
        object_storage, "gstr_input_reference",
        lambda *args: calls.append(args) or "must-not-be-issued",
    )
    denied = create(gstr_api, who=getattr(gstr_api, role))
    assert denied.status_code == 404
    assert calls == []


def test_wrong_client_cross_workspace_inactive_client_and_gstin_are_hidden(gstr_api, monkeypatch):
    calls = []
    monkeypatch.setattr(
        object_storage, "create_private_upload_target",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    login(gstr_api.client, gstr_api.owner)
    wrong_client = gstr_api.client.post(
        root(gstr_api, client_id=gstr_api.outsider[1].id),
        json=payload(gstr_api), headers=mutation_headers(gstr_api.client),
    )
    assert wrong_client.status_code == 404
    archived = gstr_api.client.post(
        root(gstr_api, client_id=gstr_api.archived_business.id),
        json=payload(gstr_api), headers=mutation_headers(gstr_api.client),
    )
    assert archived.status_code == 404
    inactive = create(
        gstr_api,
        data=payload(gstr_api, client_gstin_id=str(gstr_api.inactive_gstin.id)),
    )
    assert inactive.status_code == 404
    wrong_gstin = create(
        gstr_api,
        data=payload(gstr_api, client_gstin_id=str(gstr_api.other_gstin.id)),
    )
    assert wrong_gstin.status_code == 404
    assert calls == []


def test_create_is_idempotent_and_payload_bound(gstr_api):
    key = uuid4()
    first = create(gstr_api, key=key)
    retry = create(gstr_api, key=key)
    assert first.status_code == retry.status_code == 201
    assert first.json()["run"]["id"] == retry.json()["run"]["id"]
    changed = payload(gstr_api)
    changed["files"][0]["size_bytes"] += 1
    assert create(gstr_api, key=key, data=changed).status_code == 409
    with Session(gstr_api.engine) as db:
        assert len(db.exec(select(GstrMergeRun).where(
            GstrMergeRun.workspace_id == gstr_api.owner[1].id,
            GstrMergeRun.client_id == gstr_api.business.id,
        )).all()) == 1


def test_submit_checks_missing_zero_oversized_aggregate_and_metadata(gstr_api, monkeypatch):
    created = create(gstr_api).json()
    run = created["run"]
    missing = submit(gstr_api, run)
    assert missing.status_code == 409 and "missing" in missing.json()["detail"].lower()

    refs = [item["object_ref"] for item in stored_run(gstr_api, run["id"]).input_manifest_json["files"]]
    monkeypatch.setattr(
        object_storage, "private_object_stat",
        lambda reference: object_storage.PrivateObjectStat(0, object_storage.XLSX_MEDIA_TYPE, {}),
    )
    assert submit(gstr_api, run).status_code == 400
    monkeypatch.setattr(
        object_storage, "private_object_stat",
        lambda reference: object_storage.PrivateObjectStat(26 * 1024 * 1024, object_storage.XLSX_MEDIA_TYPE, {}),
    )
    assert submit(gstr_api, run).status_code == 413
    monkeypatch.setattr(
        object_storage, "gstr_storage_settings",
        lambda: object_storage.GstrStorageSettings(100, 30, 900),
    )
    monkeypatch.setattr(
        object_storage, "private_object_stat",
        lambda reference: object_storage.PrivateObjectStat(10, object_storage.XLSX_MEDIA_TYPE, {}),
    )
    assert submit(gstr_api, run).status_code == 413

    monkeypatch.setattr(object_storage, "gstr_reference_belongs_to_run", lambda *a, **k: True)
    monkeypatch.setattr(object_storage, "is_s3_reference", lambda reference: reference in refs)
    monkeypatch.setattr(
        object_storage, "gstr_storage_settings",
        lambda: object_storage.GstrStorageSettings(100, 100, 900),
    )
    assert submit(gstr_api, run).status_code == 409


def test_submit_is_idempotent_stays_queued_and_does_not_process(gstr_api, monkeypatch):
    created = create(gstr_api).json()
    upload_all(gstr_api, created)
    processor_calls = []
    from backend.app.services.gstr_merge import processor
    monkeypatch.setattr(
        processor, "process_workbooks", lambda *args, **kwargs: processor_calls.append(args)
    )
    key = uuid4()
    first = submit(gstr_api, created["run"], key=key)
    retry = submit(gstr_api, created["run"], key=key)
    another_retry = submit(gstr_api, created["run"], key=uuid4())
    assert first.status_code == retry.status_code == another_retry.status_code == 200
    assert {first.json()["status"], retry.json()["status"]} == {"QUEUED"}
    assert processor_calls == []
    with Session(gstr_api.engine) as db:
        events = db.exec(select(AuditEvent).where(
            AuditEvent.entity_id == created["run"]["id"],
            AuditEvent.action == "GSTR_MERGE_SUBMITTED",
        )).all()
        assert len(events) == 1


def test_storage_errors_are_sanitized_and_run_remains_uploading(gstr_api, monkeypatch):
    created = create(gstr_api).json()
    monkeypatch.setattr(
        object_storage,
        "private_object_stat",
        lambda reference: (_ for _ in ()).throw(
            object_storage.ObjectStorageUnavailableError("raw secret")
        ),
    )
    response = submit(gstr_api, created["run"])
    assert response.status_code == 503 and "secret" not in response.text
    assert stored_run(gstr_api, created["run"]["id"]).status == "UPLOADING"


@pytest.mark.parametrize("role", ["unassigned", "suspended", "removed", "outsider"])
def test_guessed_run_and_wrong_client_are_denied_before_head_delete_or_download(gstr_api, monkeypatch, role):
    created = create(gstr_api).json()
    run = created["run"]
    calls = []
    monkeypatch.setattr(
        object_storage, "private_object_stat", lambda *args: calls.append(("head", args))
    )
    monkeypatch.setattr(
        object_storage, "delete_private_object", lambda *args: calls.append(("delete", args))
    )
    login(gstr_api.client, getattr(gstr_api, role))
    guessed = uuid4()
    paths = [
        ("GET", root(gstr_api)),
        ("GET", root(gstr_api) + f"/{run['id']}"),
        ("POST", root(gstr_api) + f"/{run['id']}/submit"),
        ("GET", root(gstr_api) + f"/{run['id']}/download"),
        ("DELETE", root(gstr_api) + f"/{run['id']}"),
        ("GET", root(gstr_api) + f"/{guessed}"),
    ]
    for method, path in paths:
        response = gstr_api.client.request(
            method, path, json={} if method == "POST" else None,
            headers=mutation_headers(gstr_api.client, version=run["version"]),
        )
        assert response.status_code == 404
    login(gstr_api.client, gstr_api.owner)
    wrong_url = root(gstr_api, client_id=gstr_api.other_business.id)
    assert gstr_api.client.get(wrong_url + f"/{run['id']}/download").status_code == 404
    assert calls == []


@pytest.mark.parametrize("mutation", ["submit", "delete"])
@pytest.mark.parametrize("csrf", ["missing", "invalid"])
def test_submit_and_delete_require_csrf(gstr_api, mutation, csrf):
    created = create(gstr_api).json()
    run = created["run"]
    login(gstr_api.client, gstr_api.owner)
    path = root(gstr_api) + f"/{run['id']}"
    method = "DELETE"
    kwargs = {}
    if mutation == "submit":
        path += "/submit"
        method = "POST"
        kwargs["json"] = {}
    headers = {
        "Idempotency-Key": str(uuid4()),
        "X-Run-Version": str(run["version"]),
    }
    if csrf == "invalid":
        headers["X-CSRF-Token"] = "invalid"
    response = gstr_api.client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == 403


def test_tampered_manifest_reference_is_rejected_before_storage_access(gstr_api, monkeypatch):
    created = create(gstr_api).json()
    run = created["run"]
    with Session(gstr_api.engine) as db:
        row = db.get(GstrMergeRun, run["id"])
        manifest = dict(row.input_manifest_json)
        files = [dict(item) for item in manifest["files"]]
        files[0]["object_ref"] = object_storage.gstr_input_reference(
            row.workspace_id, gstr_api.other_business.id, row.id, files[0]["period_code"]
        )
        manifest["files"] = files
        row.input_manifest_json = manifest
        db.add(row)
        db.commit()
    calls = []
    monkeypatch.setattr(
        object_storage, "private_object_stat", lambda *args: calls.append(args)
    )
    response = submit(gstr_api, run)
    assert response.status_code == 409
    assert calls == []


def test_download_states_expiry_and_audit(gstr_api):
    created = create(gstr_api).json()["run"]
    login(gstr_api.client, gstr_api.owner)
    path = root(gstr_api) + f"/{created['id']}/download"
    assert gstr_api.client.get(path).status_code == 409
    with Session(gstr_api.engine) as db:
        row = db.get(GstrMergeRun, created["id"])
        row.status = "EXPIRED"
        row.expires_at = utcnow()
        db.add(row)
        db.commit()
    assert gstr_api.client.get(path).status_code == 410


def test_delete_partial_cleanup_failure_is_retryable_and_audited(gstr_api, monkeypatch):
    created = create(gstr_api).json()
    run = created["run"]
    calls = []

    def fail_second(reference):
        calls.append(reference)
        if len(calls) == 2:
            raise object_storage.ObjectStorageUnavailableError("provider detail")

    monkeypatch.setattr(object_storage, "delete_private_object", fail_second)
    login(gstr_api.client, gstr_api.owner)
    path = root(gstr_api) + f"/{run['id']}"
    failed = gstr_api.client.delete(
        path, headers=mutation_headers(gstr_api.client, version=run["version"])
    )
    assert failed.status_code == 503 and "provider detail" not in failed.text
    assert stored_run(gstr_api, run["id"]).status == "UPLOADING"
    monkeypatch.setattr(
        object_storage, "delete_private_object", lambda reference: calls.append(reference)
    )
    retry = gstr_api.client.delete(
        path, headers=mutation_headers(gstr_api.client, version=run["version"])
    )
    assert retry.status_code == 200
    with Session(gstr_api.engine) as db:
        row = db.get(GstrMergeRun, run["id"])
        assert row.status == "DELETED" and row.deleted_at and row.lease_token is None
        assert all("object_ref" not in item for item in row.input_manifest_json["files"])
        actions = [event.action for event in db.exec(select(AuditEvent).where(
            AuditEvent.entity_id == row.id,
            AuditEvent.action.in_(("GSTR_MERGE_ARTIFACTS_DELETED", "GSTR_MERGE_DELETED")),
        )).all()]
        assert set(actions) == {"GSTR_MERGE_ARTIFACTS_DELETED", "GSTR_MERGE_DELETED"}


def test_mutations_enforce_auth_csrf_and_expected_user(gstr_api, monkeypatch):
    path = root(gstr_api)
    login(gstr_api.client, gstr_api.owner)
    assert gstr_api.client.post(path, json=payload(gstr_api)).status_code == 403
    invalid = mutation_headers(gstr_api.client)
    invalid["X-CSRF-Token"] = "invalid"
    assert gstr_api.client.post(path, json=payload(gstr_api), headers=invalid).status_code == 403
    mismatch = mutation_headers(
        gstr_api.client, expected_user=gstr_api.outsider[0].id
    )
    assert gstr_api.client.post(path, json=payload(gstr_api), headers=mismatch).status_code == 409
    gstr_api.client.cookies.clear()
    csrf = gstr_api.client.get("/api/auth/csrf")
    assert gstr_api.client.post(
        path, json=payload(gstr_api),
        headers={"X-CSRF-Token": csrf.json()["csrf_token"], "Idempotency-Key": str(uuid4())},
    ).status_code == 401
