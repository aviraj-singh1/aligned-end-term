"""PostgreSQL API tests for Tally Export V1 configuration routes."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from backend.app.api.routes.tally_exports import router as tally_router
from backend.app.db.models import (
    Client, ClientAssignment, ClientGSTIN, ReconciliationRun,
    TallyExportBatch, TallyExportCandidateSnapshot, TallyExportItem, TallyExportProfile,
)
from backend.app.models.tally_export import (
    ReverseChargeStatus, TallyExportCandidate, TallyExportTransactionType,
    TallyRateAllocation,
)
from backend.app.services import tally_export_configuration_service as service
from backend.app.services import tally_export_batch_service as batch_service
from backend.app.services import tally_export_workflow_service as workflow
from backend.app.services.tally_export_candidate_service import _snapshot_row
from backend.tests.test_auth_api import auth_api
from backend.tests.test_database_schema import schema_engine
from backend.tests.test_workspace_api import actor, login


pytestmark = pytest.mark.postgres


@dataclass
class TallyApiGraph:
    http: object
    engine: object
    owner: tuple
    admin: tuple
    assigned: tuple
    unassigned: tuple
    outsider: tuple
    client: Client
    gstin: ClientGSTIN
    other_client: Client
    other_gstin: ClientGSTIN
    run: ReconciliationRun
    candidate_ids: tuple[str, ...]
    artifact_root: object


@pytest.fixture
def tally_api(auth_api, monkeypatch, tmp_path):
    http, _, engine = auth_api
    monkeypatch.setattr(service, "get_database_engine", lambda: engine)
    monkeypatch.setattr(workflow, "get_database_engine", lambda: engine)
    monkeypatch.setattr(batch_service, "get_database_engine", lambda: engine)
    monkeypatch.setattr(batch_service, "local_storage_root", lambda: tmp_path)
    monkeypatch.setattr(batch_service, "storage_backend", lambda: "local")
    http.app.include_router(tally_router, prefix="/api")

    owner = actor(engine)
    admin = actor(engine, workspace=owner[1], role="ADMIN")
    assigned = actor(engine, workspace=owner[1], role="ACCOUNTANT")
    unassigned = actor(engine, workspace=owner[1], role="ACCOUNTANT")
    outsider = actor(engine)
    with Session(engine, expire_on_commit=False) as db:
        client = Client(workspace_id=owner[1].id, name="Tally client", created_by=owner[0].id)
        other_client = Client(
            workspace_id=owner[1].id, name="Other Tally client", created_by=owner[0].id
        )
        db.add_all([client, other_client])
        db.flush()
        gstin = ClientGSTIN(
            workspace_id=owner[1].id, client_id=client.id, gstin="06AAAAA0000A1Z5"
        )
        other_gstin = ClientGSTIN(
            workspace_id=owner[1].id, client_id=other_client.id, gstin="07AAAAA0000A1Z4"
        )
        db.add_all([gstin, other_gstin])
        db.flush()
        db.add(ClientAssignment(
            workspace_id=owner[1].id,
            client_id=client.id,
            membership_id=assigned[2].id,
            assigned_by=owner[0].id,
        ))
        run = ReconciliationRun(
            workspace_id=owner[1].id,
            client_id=client.id,
            client_gstin_id=gstin.id,
            created_by=owner[0].id,
            redis_session_id=uuid4(),
            status="COMPLETED",
            result_version=1,
            completed_at=datetime.now(timezone.utc),
            session_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(run)
        db.flush()
        candidates = (
            purchase_candidate("03DEMOX1234A1Z5", "Punjab Supplier", "TEST-18", "18", "1000", "180"),
            purchase_candidate("03DEMOX1234A1Z5", "Punjab Supplier", "TEST-05", "5", "2000", "100"),
        )
        snapshots = tuple(_snapshot_row(run, gstin.gstin, candidate) for candidate in candidates)
        db.add_all(snapshots)
        run.tally_candidate_snapshot_version = 1
        db.add(run)
        db.commit()
    return TallyApiGraph(
        http, engine, owner, admin, assigned, unassigned, outsider,
        client, gstin, other_client, other_gstin, run,
        tuple(row.candidate_id for row in snapshots), tmp_path,
    )


def purchase_candidate(supplier_gstin, supplier_name, document_number, rate, taxable, igst):
    taxable_value = Decimal(taxable)
    tax = Decimal(igst)
    gst_rate = Decimal(rate)
    return TallyExportCandidate(
        transaction_type=TallyExportTransactionType.PURCHASE_INVOICE,
        supplier_gstin=supplier_gstin,
        supplier_gstin_valid=True,
        supplier_name=supplier_name,
        document_number=document_number,
        document_date=date(2026, 4, 1),
        original_invoice_number=document_number,
        taxable_value=taxable_value,
        igst=tax,
        cgst=Decimal("0"),
        sgst=Decimal("0"),
        cess=Decimal("0"),
        cess_present=False,
        invoice_total=taxable_value + tax,
        gst_rates=(gst_rate,),
        rate_allocations=(TallyRateAllocation(
            gst_rate=gst_rate, taxable_value=taxable_value, igst=tax,
            cgst=Decimal("0"), sgst=Decimal("0"), cess=Decimal("0"),
        ),),
        gst_rate_missing=False,
        place_of_supply="Punjab",
        place_of_supply_values=("Punjab",),
        place_of_supply_missing=False,
        place_of_supply_conflicting=False,
        reverse_charge_status=ReverseChargeStatus.NO,
        reverse_charge_values=("No",),
        is_amendment=False,
        amendment_original_number="",
        amendment_original_date=None,
        is_cancelled=False,
        cancellation_evidence=(),
        source_rows=(),
        financial_parse_issues=(),
    )


def root(graph, *, client_id=None, gstin_id=None, workspace_id=None):
    return (
        f"/api/workspaces/{workspace_id or graph.owner[1].id}/clients/"
        f"{client_id or graph.client.id}/gstins/{gstin_id or graph.gstin.id}/tally-export"
    )


def csrf_headers(graph):
    response = graph.http.get("/api/auth/csrf")
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def mutate(graph, method, path, payload=None, *, who=None):
    login(graph.http, who or graph.owner)
    return graph.http.request(
        method, path, json=payload, headers=csrf_headers(graph)
    )


def profile_payload(**overrides):
    payload = {
        "company_name": "Example Books",
        "tally_version": "7.1",
        "purchase_voucher_type": "Purchase",
        "debit_note_voucher_type": "Debit Note",
        "igst_ledger_name": "Input IGST",
        "cgst_ledger_name": "Input CGST",
        "sgst_ledger_name": "Input SGST",
        "numbering_strategy": "TALLY_AUTO",
    }
    payload.update(overrides)
    return payload


def create_profile(graph, *, who=None):
    return mutate(graph, "POST", root(graph) + "/profile", profile_payload(), who=who)


def run_root(graph, *, session_id=None, client_id=None, gstin_id=None, workspace_id=None):
    return (
        root(graph, client_id=client_id, gstin_id=gstin_id, workspace_id=workspace_id)
        + f"/runs/{session_id or graph.run.redis_session_id}"
    )


def configure_for_validation(graph):
    assert create_profile(graph).status_code == 201
    assert mutate(graph, "POST", root(graph) + "/party-mappings", {
        "supplier_gstin": "03DEMOX1234A1Z5",
        "source_supplier_name": "Punjab Supplier",
        "tally_party_ledger_name": "Punjab Supplier Ledger",
    }, who=graph.assigned).status_code == 201
    for rate in ("5.000", "18.000"):
        assert mutate(graph, "POST", root(graph) + "/purchase-ledger-mappings", {
            "transaction_kind": "purchase_invoice",
            "gst_rate": rate,
            "tally_purchase_ledger_name": f"Purchase {rate}%",
        }).status_code == 201


def generate(graph, *, candidate_ids=None, key=None, allow_reexport=False, who=None,
             expected_run_version=1, csrf=True):
    login(graph.http, who or graph.owner)
    headers = {"Idempotency-Key": str(key or uuid4())}
    if csrf:
        headers.update(csrf_headers(graph))
    return graph.http.post(run_root(graph) + "/batches", json={
        "candidate_ids": candidate_ids or [graph.candidate_ids[0]],
        "expected_run_version": expected_run_version,
        "allow_reexport": allow_reexport,
    }, headers=headers)


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_and_admin_can_create_profile_and_assigned_accountant_can_read(tally_api, role):
    created = create_profile(tally_api, who=getattr(tally_api, role))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["tally_version"] == "7.1"
    assert body["numbering_strategy"] == "TALLY_AUTO"
    assert not {"created_by", "updated_by", "artifact_object_ref"} & body.keys()

    login(tally_api.http, tally_api.assigned)
    read = tally_api.http.get(root(tally_api) + "/profile")
    assert read.status_code == 200 and read.json() == body


def test_profile_validation_roles_conflicts_and_tenant_isolation(tally_api):
    login(tally_api.http, tally_api.assigned)
    denied_create = tally_api.http.post(
        root(tally_api) + "/profile", json=profile_payload(), headers=csrf_headers(tally_api)
    )
    assert denied_create.status_code == 403

    created = create_profile(tally_api).json()
    update = profile_payload(expected_version=created["version"], status="ACTIVE")
    login(tally_api.http, tally_api.assigned)
    denied_update = tally_api.http.put(
        root(tally_api) + "/profile", json=update, headers=csrf_headers(tally_api)
    )
    assert denied_update.status_code == 403

    login(tally_api.http, tally_api.owner)
    stale = tally_api.http.put(
        root(tally_api) + "/profile",
        json={**update, "expected_version": 999}, headers=csrf_headers(tally_api),
    )
    assert stale.status_code == 409
    unsupported_version = tally_api.http.put(
        root(tally_api) + "/profile",
        json={**update, "tally_version": "7.0"}, headers=csrf_headers(tally_api),
    )
    assert unsupported_version.status_code == 422
    unsupported_numbering = tally_api.http.put(
        root(tally_api) + "/profile",
        json={**update, "numbering_strategy": "ALIGNED_GENERATED"},
        headers=csrf_headers(tally_api),
    )
    assert unsupported_numbering.status_code == 422

    login(tally_api.http, tally_api.unassigned)
    assert tally_api.http.get(root(tally_api) + "/profile").status_code == 404
    login(tally_api.http, tally_api.outsider)
    assert tally_api.http.get(root(tally_api) + "/profile").status_code == 404
    login(tally_api.http, tally_api.owner)
    assert tally_api.http.get(root(
        tally_api, gstin_id=tally_api.other_gstin.id
    ) + "/profile").status_code == 404


def test_party_mapping_crud_is_exact_versioned_and_accountant_accessible(tally_api):
    assert create_profile(tally_api).status_code == 201
    party = {
        "supplier_gstin": "03aaack5657l1ze",
        "source_supplier_name": "Supplier Private Limited",
        "tally_party_ledger_name": "Supplier Ledger",
    }
    created = mutate(
        tally_api, "POST", root(tally_api) + "/party-mappings", party,
        who=tally_api.assigned,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["supplier_gstin"] == "03DEMOX1234A1Z5"
    assert "confirmed_by" not in body

    login(tally_api.http, tally_api.assigned)
    listed = tally_api.http.get(root(tally_api) + "/party-mappings")
    assert listed.status_code == 200
    assert [item["supplier_gstin"] for item in listed.json()["items"]] == ["03DEMOX1234A1Z5"]

    update = {
        "expected_version": body["version"],
        "source_supplier_name": "Supplier Pvt Ltd",
        "tally_party_ledger_name": "Confirmed Supplier Ledger",
    }
    updated = mutate(
        tally_api, "PUT",
        root(tally_api) + "/party-mappings/03DEMOX1234A1Z5", update,
        who=tally_api.assigned,
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    stale = mutate(
        tally_api, "PUT",
        root(tally_api) + "/party-mappings/03DEMOX1234A1Z5", update,
        who=tally_api.assigned,
    )
    assert stale.status_code == 409
    retired = mutate(
        tally_api, "DELETE",
        root(tally_api) + "/party-mappings/03DEMOX1234A1Z5?expected_version=2",
        who=tally_api.assigned,
    )
    assert retired.status_code == 200 and retired.json()["status"] == "INACTIVE"


def test_party_mapping_validation_and_tenant_isolation(tally_api):
    assert create_profile(tally_api).status_code == 201
    invalid = mutate(tally_api, "POST", root(tally_api) + "/party-mappings", {
        "supplier_gstin": "invalid",
        "source_supplier_name": "Supplier",
        "tally_party_ledger_name": "Supplier Ledger",
    })
    assert invalid.status_code == 422
    login(tally_api.http, tally_api.unassigned)
    assert tally_api.http.get(root(tally_api) + "/party-mappings").status_code == 404
    login(tally_api.http, tally_api.owner)
    assert tally_api.http.get(root(
        tally_api, gstin_id=tally_api.other_gstin.id
    ) + "/party-mappings").status_code == 404


def test_purchase_mapping_crud_v1_rules_and_roles(tally_api):
    assert create_profile(tally_api).status_code == 201
    payload = {
        "transaction_kind": "purchase_invoice",
        "gst_rate": "18.000",
        "tally_purchase_ledger_name": "Purchase 18%",
    }
    created = mutate(
        tally_api, "POST", root(tally_api) + "/purchase-ledger-mappings", payload
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["gst_rate"] == "18.000"
    assert not {"created_by", "updated_by"} & body.keys()

    login(tally_api.http, tally_api.assigned)
    listed = tally_api.http.get(root(tally_api) + "/purchase-ledger-mappings")
    assert listed.status_code == 200 and listed.json()["items"] == [body]
    denied = tally_api.http.post(
        root(tally_api) + "/purchase-ledger-mappings",
        json={**payload, "gst_rate": "5.000"}, headers=csrf_headers(tally_api),
    )
    assert denied.status_code == 403

    updated = mutate(
        tally_api, "PUT",
        root(tally_api) + "/purchase-ledger-mappings/purchase_invoice/18.000",
        {"expected_version": 1, "tally_purchase_ledger_name": "Purchases @ 18%", "status": "INACTIVE"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2 and updated.json()["status"] == "INACTIVE"
    stale = mutate(
        tally_api, "PUT",
        root(tally_api) + "/purchase-ledger-mappings/purchase_invoice/18.000",
        {"expected_version": 1, "tally_purchase_ledger_name": "Stale", "status": "ACTIVE"},
    )
    assert stale.status_code == 409

    unsupported = mutate(
        tally_api, "POST", root(tally_api) + "/purchase-ledger-mappings",
        {**payload, "transaction_kind": "supplier_credit_note", "gst_rate": "12.000"},
    )
    assert unsupported.status_code == 422
    invalid_rate = mutate(
        tally_api, "POST", root(tally_api) + "/purchase-ledger-mappings",
        {**payload, "gst_rate": "18.1234"},
    )
    assert invalid_rate.status_code == 422


def test_purchase_mapping_tenant_isolation_csrf_and_strict_payloads(tally_api):
    assert create_profile(tally_api).status_code == 201
    payload = {
        "transaction_kind": "purchase_invoice",
        "gst_rate": "5.000",
        "tally_purchase_ledger_name": "Purchase 5%",
    }
    login(tally_api.http, tally_api.owner)
    assert tally_api.http.post(
        root(tally_api) + "/purchase-ledger-mappings", json=payload
    ).status_code == 403
    extra = mutate(
        tally_api, "POST", root(tally_api) + "/purchase-ledger-mappings",
        {**payload, "artifact_object_ref": "private://must-not-be-accepted"},
    )
    assert extra.status_code == 422

    login(tally_api.http, tally_api.unassigned)
    assert tally_api.http.get(root(tally_api) + "/purchase-ledger-mappings").status_code == 404
    login(tally_api.http, tally_api.owner)
    assert tally_api.http.get(root(
        tally_api, gstin_id=tally_api.other_gstin.id
    ) + "/purchase-ledger-mappings").status_code == 404


def test_candidates_use_durable_snapshot_with_search_pagination_and_requirements(tally_api):
    login(tally_api.http, tally_api.assigned)
    response = tally_api.http.get(run_root(tally_api) + "/candidates?page=1&page_size=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == str(tally_api.run.redis_session_id)
    assert body["reconciliation_run_id"] == str(tally_api.run.id)
    assert body["run_version"] == 1
    assert body["total"] == 2 and len(body["items"]) == 1
    assert body["requirements"] == {
        "unresolved_suppliers": [{
            "supplier_gstin": "03DEMOX1234A1Z5",
            "supplier_name": "Punjab Supplier",
            "candidate_count": 2,
        }],
        "unresolved_purchase_rates": [
            {"transaction_kind": "purchase_invoice", "gst_rate": "5.000", "candidate_count": 1},
            {"transaction_kind": "purchase_invoice", "gst_rate": "18.000", "candidate_count": 1},
        ],
    }
    assert body["items"][0]["candidate_id"] in tally_api.candidate_ids
    assert body["items"][0]["party_mapping_status"] == "UNMAPPED"
    assert not {
        "transaction_fingerprint", "remote_id", "source_rows", "artifact_object_ref",
        "created_by", "updated_by", "filesystem_path",
    } & body["items"][0].keys()

    searched = tally_api.http.get(run_root(tally_api) + "/candidates?search=TEST-18")
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["document_number"] == "TEST-18"
    assert searched.json()["requirements"]["unresolved_purchase_rates"] == [{
        "transaction_kind": "purchase_invoice", "gst_rate": "18.000", "candidate_count": 1,
    }]


def test_candidate_requirements_disappear_after_exact_active_mappings(tally_api):
    configure_for_validation(tally_api)
    login(tally_api.http, tally_api.assigned)
    response = tally_api.http.get(run_root(tally_api) + "/candidates")
    assert response.status_code == 200
    body = response.json()
    assert body["requirements"] == {
        "unresolved_suppliers": [], "unresolved_purchase_rates": [],
    }
    assert {item["party_mapping_status"] for item in body["items"]} == {"MAPPED"}


def test_candidates_report_prior_exact_export_without_exposing_fingerprint(tally_api):
    configure_for_validation(tally_api)
    with Session(tally_api.engine) as db:
        profile = db.exec(select(TallyExportProfile).where(
            TallyExportProfile.client_gstin_id == tally_api.gstin.id
        )).one()
        snapshot = db.exec(select(TallyExportCandidateSnapshot).where(
            TallyExportCandidateSnapshot.reconciliation_run_id == tally_api.run.id,
            TallyExportCandidateSnapshot.candidate_id == tally_api.candidate_ids[0],
        )).one()
        batch = TallyExportBatch(
            workspace_id=tally_api.owner[1].id, client_id=tally_api.client.id,
            client_gstin_id=tally_api.gstin.id, reconciliation_run_id=tally_api.run.id,
            profile_id=profile.id, profile_version=profile.version, tally_version="7.1",
            status="GENERATED", selected_count=1, exported_count=1,
            created_by=tally_api.owner[0].id,
        )
        db.add(batch)
        db.flush()
        db.add(TallyExportItem(
            workspace_id=tally_api.owner[1].id, client_id=tally_api.client.id,
            client_gstin_id=tally_api.gstin.id, batch_id=batch.id,
            candidate_id=snapshot.candidate_id,
            transaction_fingerprint=snapshot.transaction_fingerprint,
            remote_id=snapshot.remote_id, document_kind="purchase_invoice", outcome="EXPORTED",
        ))
        db.commit()
    login(tally_api.http, tally_api.owner)
    body = tally_api.http.get(run_root(tally_api) + "/candidates").json()
    exported = {item["candidate_id"]: item["previously_exported"] for item in body["items"]}
    assert exported[tally_api.candidate_ids[0]] is True
    assert exported[tally_api.candidate_ids[1]] is False
    assert "fingerprint" not in str(body).casefold()


def test_candidate_access_is_tenant_safe_and_legacy_snapshot_fails_explicitly(tally_api):
    login(tally_api.http, tally_api.unassigned)
    assert tally_api.http.get(run_root(tally_api) + "/candidates").status_code == 404
    login(tally_api.http, tally_api.outsider)
    assert tally_api.http.get(run_root(tally_api) + "/candidates").status_code == 404
    login(tally_api.http, tally_api.owner)
    assert tally_api.http.get(run_root(
        tally_api, gstin_id=tally_api.other_gstin.id
    ) + "/candidates").status_code == 404
    assert tally_api.http.get(run_root(
        tally_api, session_id=uuid4()
    ) + "/candidates").status_code == 404

    with Session(tally_api.engine) as db:
        run = db.get(ReconciliationRun, tally_api.run.id)
        run.tally_candidate_snapshot_version = None
        db.add(run)
        db.commit()
    legacy = tally_api.http.get(run_root(tally_api) + "/candidates")
    assert legacy.status_code == 409
    assert "rerun reconciliation" in legacy.json()["detail"]


def test_bulk_validation_reports_blockers_and_fully_configured_candidates(tally_api):
    missing_profile = mutate(tally_api, "POST", run_root(tally_api) + "/validate", {
        "candidate_ids": [tally_api.candidate_ids[0]], "expected_run_version": 1,
    })
    assert missing_profile.status_code == 200, missing_profile.text
    codes = {item["code"] for item in missing_profile.json()["items"][0]["blockers"]}
    assert {"MISSING_TALLY_PROFILE", "MISSING_PARTY_LEDGER_MAPPING",
            "MISSING_PURCHASE_LEDGER_MAPPING"} <= codes

    configure_for_validation(tally_api)
    valid = mutate(tally_api, "POST", run_root(tally_api) + "/validate", {
        "candidate_ids": list(tally_api.candidate_ids), "expected_run_version": 1,
    }, who=tally_api.assigned)
    assert valid.status_code == 200, valid.text
    body = valid.json()
    assert body["exportable_count"] == 2 and body["blocked_count"] == 0
    assert all(item["eligible"] for item in body["items"])
    assert {item["resolved"]["tally_version"] for item in body["items"]} == {"7.1"}
    assert not {"remote_id", "fingerprint"} & body["items"][0].keys()


def test_bulk_validation_rejects_stale_unknown_duplicate_and_missing_csrf(tally_api):
    path = run_root(tally_api) + "/validate"
    stale = mutate(tally_api, "POST", path, {
        "candidate_ids": [tally_api.candidate_ids[0]], "expected_run_version": 2,
    })
    assert stale.status_code == 409
    unknown = mutate(tally_api, "POST", path, {
        "candidate_ids": [f"purchase_invoice:{uuid4()}"], "expected_run_version": 1,
    })
    assert unknown.status_code == 409
    duplicate = mutate(tally_api, "POST", path, {
        "candidate_ids": [tally_api.candidate_ids[0], tally_api.candidate_ids[0]],
        "expected_run_version": 1,
    })
    assert duplicate.status_code == 422
    login(tally_api.http, tally_api.owner)
    no_csrf = tally_api.http.post(path, json={
        "candidate_ids": [tally_api.candidate_ids[0]], "expected_run_version": 1,
    })
    assert no_csrf.status_code == 403


def test_bulk_validation_allows_assigned_and_denies_cross_tenant(tally_api):
    payload = {"candidate_ids": [tally_api.candidate_ids[0]], "expected_run_version": 1}
    assert mutate(
        tally_api, "POST", run_root(tally_api) + "/validate", payload,
        who=tally_api.assigned,
    ).status_code == 200
    assert mutate(
        tally_api, "POST", run_root(tally_api) + "/validate", payload,
        who=tally_api.unassigned,
    ).status_code == 404
    assert mutate(
        tally_api, "POST", run_root(tally_api, gstin_id=tally_api.other_gstin.id) + "/validate",
        payload,
    ).status_code == 404


def test_generation_persists_private_xml_downloads_and_reuses_idempotently(tally_api):
    configure_for_validation(tally_api)
    key = uuid4()
    first = generate(tally_api, key=key, who=tally_api.assigned)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["status"] == "GENERATED" and body["generated_count"] == 1
    assert body["candidate_count"] == 1 and body["failed_count"] == 0
    assert body["checksum_sha256"] and body["download_url"].endswith("/download")
    assert body["reused"] is False
    assert not {"artifact_object_ref", "operation_key", "request_fingerprint"} & body.keys()
    files = list((tally_api.artifact_root / "tally-exports").glob("*.xml"))
    assert len(files) == 1

    retry = generate(tally_api, key=key, who=tally_api.assigned)
    assert retry.status_code == 201
    assert retry.json()["batch_id"] == body["batch_id"]
    assert retry.json()["reused"] is True
    assert len(list((tally_api.artifact_root / "tally-exports").glob("*.xml"))) == 1

    login(tally_api.http, tally_api.assigned)
    downloaded = tally_api.http.get(body["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/xml")
    assert "Tally-Purchases-" in downloaded.headers["content-disposition"]
    assert downloaded.content.startswith(b"<?xml")


def test_generation_supports_multiple_candidates_with_distinct_rate_ledgers(tally_api):
    configure_for_validation(tally_api)
    response = generate(tally_api, candidate_ids=list(tally_api.candidate_ids))
    assert response.status_code == 201, response.text
    assert response.json()["candidate_count"] == 2
    assert response.json()["generated_count"] == 2
    login(tally_api.http, tally_api.owner)
    detail = tally_api.http.get(
        root(tally_api) + f"/batches/{response.json()['batch_id']}"
    )
    assert detail.status_code == 200
    assert {item["document_number"] for item in detail.json()["items"]} == {
        "TEST-18", "TEST-05",
    }


def test_generation_revalidates_and_maps_conflicts_and_request_protection(tally_api):
    path = run_root(tally_api) + "/batches"
    login(tally_api.http, tally_api.owner)
    payload = {
        "candidate_ids": [tally_api.candidate_ids[0]],
        "expected_run_version": 1,
        "allow_reexport": False,
    }
    assert tally_api.http.post(path, json=payload, headers=csrf_headers(tally_api)).status_code == 422
    assert generate(tally_api, csrf=False).status_code == 403
    assert generate(tally_api).status_code == 409  # Missing profile/mappings are revalidated.
    assert generate(tally_api, expected_run_version=2).status_code == 409
    assert generate(tally_api, candidate_ids=[f"purchase_invoice:{uuid4()}"]).status_code == 409

    configure_for_validation(tally_api)
    key = uuid4()
    assert generate(tally_api, key=key).status_code == 201
    changed_selection = generate(
        tally_api, key=key, candidate_ids=[tally_api.candidate_ids[1]], allow_reexport=True
    )
    assert changed_selection.status_code == 409

    party = tally_api.http.get(root(tally_api) + "/party-mappings").json()["items"][0]
    mapping_update = mutate(
        tally_api, "PUT", root(tally_api) + "/party-mappings/03DEMOX1234A1Z5",
        {"expected_version": party["version"], "source_supplier_name": "Punjab Supplier",
         "tally_party_ledger_name": "Changed Punjab Ledger"},
        who=tally_api.assigned,
    )
    assert mapping_update.status_code == 200
    changed_mapping = generate(tally_api, key=key, allow_reexport=True)
    assert changed_mapping.status_code == 409

    profile = tally_api.http.get(root(tally_api) + "/profile").json()
    update = profile_payload(expected_version=profile["version"], status="ACTIVE")
    assert mutate(tally_api, "PUT", root(tally_api) + "/profile", update).status_code == 200
    changed_config = generate(tally_api, key=key, allow_reexport=True)
    assert changed_config.status_code == 409


def test_generation_authorization_and_same_key_isolated_by_actor(tally_api):
    configure_for_validation(tally_api)
    key = uuid4()
    owner = generate(tally_api, key=key, who=tally_api.owner)
    assert owner.status_code == 201
    assigned = generate(
        tally_api, key=key, who=tally_api.assigned, allow_reexport=True
    )
    assert assigned.status_code == 201
    assert assigned.json()["batch_id"] != owner.json()["batch_id"]
    assert generate(tally_api, who=tally_api.unassigned).status_code == 404

    login(tally_api.http, tally_api.owner)
    cross = tally_api.http.post(
        run_root(tally_api, gstin_id=tally_api.other_gstin.id) + "/batches",
        json={"candidate_ids": [tally_api.candidate_ids[0]], "expected_run_version": 1,
              "allow_reexport": False},
        headers={**csrf_headers(tally_api), "Idempotency-Key": str(uuid4())},
    )
    assert cross.status_code == 404


def test_history_and_detail_are_durable_ordered_and_safe(tally_api):
    configure_for_validation(tally_api)
    first = generate(tally_api, candidate_ids=[tally_api.candidate_ids[0]]).json()
    second = generate(tally_api, candidate_ids=[tally_api.candidate_ids[1]]).json()
    login(tally_api.http, tally_api.owner)
    history = tally_api.http.get(root(tally_api) + "/batches?page=1&page_size=1")
    assert history.status_code == 200
    assert history.json()["total"] == 2
    assert history.json()["items"][0]["batch_id"] == second["batch_id"]
    filtered = tally_api.http.get(
        root(tally_api) + f"/batches?session_id={tally_api.run.redis_session_id}"
    )
    assert filtered.status_code == 200 and filtered.json()["total"] == 2

    detail = tally_api.http.get(root(tally_api) + f"/batches/{first['batch_id']}")
    assert detail.status_code == 200
    item = detail.json()["items"][0]
    assert item["supplier_gstin"] == "03DEMOX1234A1Z5"
    assert item["document_number"] == "TEST-18"
    assert item["outcome"] == "EXPORTED"
    forbidden = {"transaction_fingerprint", "remote_id", "source_identity_json",
                 "artifact_object_ref", "operation_key", "request_fingerprint"}
    assert not forbidden & item.keys()
    assert not any(value in detail.text for value in (str(tally_api.artifact_root), "tally-exports"))

    login(tally_api.http, tally_api.unassigned)
    assert tally_api.http.get(root(tally_api) + "/batches").status_code == 404
    assert tally_api.http.get(root(tally_api) + f"/batches/{first['batch_id']}").status_code == 404


def test_failed_batch_detail_is_sanitized_and_not_downloadable(tally_api, monkeypatch):
    configure_for_validation(tally_api)

    def fail_storage(*_):
        raise OSError(r"secret C:\private\token.xml")

    monkeypatch.setattr(batch_service, "persist_tally_xml_artifact", fail_storage)
    failed = generate(tally_api)
    assert failed.status_code == 503 and "secret" not in failed.text.casefold()
    login(tally_api.http, tally_api.owner)
    history = tally_api.http.get(root(tally_api) + "/batches").json()
    batch_id = history["items"][0]["batch_id"]
    detail = tally_api.http.get(root(tally_api) + f"/batches/{batch_id}")
    assert detail.status_code == 200
    assert detail.json()["failure_code"] == "ARTIFACT_PERSISTENCE_FAILED"
    assert detail.json()["failure_message"] == "Private XML artifact persistence failed"
    assert detail.json()["items"][0]["outcome"] == "SKIPPED"
    assert tally_api.http.get(root(tally_api) + f"/batches/{batch_id}/download").status_code == 409


def test_missing_artifact_and_cross_tenant_download_are_hidden(tally_api):
    configure_for_validation(tally_api)
    created = generate(tally_api).json()
    with Session(tally_api.engine) as db:
        profile = db.exec(select(TallyExportProfile).where(
            TallyExportProfile.client_gstin_id == tally_api.gstin.id
        )).one()
        pending = TallyExportBatch(
            workspace_id=tally_api.owner[1].id, client_id=tally_api.client.id,
            client_gstin_id=tally_api.gstin.id, reconciliation_run_id=tally_api.run.id,
            profile_id=profile.id, profile_version=profile.version, tally_version="7.1",
            status="PENDING", selected_count=0, created_by=tally_api.owner[0].id,
        )
        db.add(pending)
        db.commit()
        pending_id = pending.id
    path = next((tally_api.artifact_root / "tally-exports").glob("*.xml"))
    path.unlink()
    login(tally_api.http, tally_api.owner)
    assert tally_api.http.get(created["download_url"]).status_code == 410
    assert tally_api.http.get(
        root(tally_api) + f"/batches/{pending_id}/download"
    ).status_code == 409
    wrong = (
        root(tally_api, gstin_id=tally_api.other_gstin.id)
        + f"/batches/{created['batch_id']}/download"
    )
    assert tally_api.http.get(wrong).status_code == 404
