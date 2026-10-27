"""PostgreSQL tests for persistent Tally export configuration and history."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import os
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import (
    AuditEvent, Client, ClientAssignment, ClientGSTIN, ReconciliationRun,
    TallyExportBatch, TallyExportCandidateSnapshot, TallyExportItem, TallyExportProfile,
    TallyPartyLedgerMapping, TallyPurchaseLedgerMapping, User, Workspace, WorkspaceMembership,
)
from backend.app.models.records import ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType
from backend.app.models.reconciliation import ReconciliationSummary
from backend.app.models.tally_export_validation import TallyWarningCode
from backend.app.services import tally_export_configuration_service as service
from backend.app.services import tally_export_batch_service as batch_service
from backend.app.services import tally_export_candidate_service as candidate_service
from backend.app.services import tally_export_validation_service as validation_service
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.reconciliation_engine import aggregate_gst_invoices
from backend.app.services.tally_export_contract import purchase_candidate
from backend.tests.test_database_schema import schema_engine


pytestmark = pytest.mark.postgres


@dataclass
class Graph:
    owner: User
    admin: User
    assigned: User
    unassigned: User
    workspace: Workspace
    client: Client
    gstin: ClientGSTIN
    other_client: Client
    other_gstin: ClientGSTIN
    run: ReconciliationRun
    other_workspace: Workspace
    outsider: User
    outsider_client: Client
    outsider_gstin: ClientGSTIN


@pytest.fixture
def graph(schema_engine, monkeypatch):
    monkeypatch.setattr(service, "get_database_engine", lambda: schema_engine)
    monkeypatch.setattr(batch_service, "get_database_engine", lambda: schema_engine)
    monkeypatch.setattr(candidate_service, "get_database_engine", lambda: schema_engine)
    monkeypatch.setattr(validation_service, "get_database_engine", lambda: schema_engine)

    def person(db, label):
        user = User(
            name=label,
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
        admin = person(db, "admin")
        assigned = person(db, "assigned")
        unassigned = person(db, "unassigned")
        workspace = Workspace(name="Tally workspace", type="FIRM", created_by=owner.id)
        db.add(workspace)
        db.flush()
        owner_membership = WorkspaceMembership(
            workspace_id=workspace.id, user_id=owner.id, role="OWNER", joined_at=utcnow()
        )
        assigned_membership = WorkspaceMembership(
            workspace_id=workspace.id, user_id=assigned.id, role="ACCOUNTANT", joined_at=utcnow()
        )
        db.add_all([
            owner_membership,
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=admin.id, role="ADMIN", joined_at=utcnow()
            ),
            assigned_membership,
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=unassigned.id, role="ACCOUNTANT", joined_at=utcnow()
            ),
        ])
        db.flush()
        client = Client(workspace_id=workspace.id, name="Primary client", created_by=owner.id)
        other_client = Client(workspace_id=workspace.id, name="Other client", created_by=owner.id)
        db.add_all([client, other_client])
        db.flush()
        gstin = ClientGSTIN(workspace_id=workspace.id, client_id=client.id, gstin="06AAAAA0000A1Z5")
        other_gstin = ClientGSTIN(workspace_id=workspace.id, client_id=other_client.id, gstin="07AAAAA0000A1Z4")
        db.add_all([gstin, other_gstin])
        db.flush()
        db.add(ClientAssignment(
            workspace_id=workspace.id,
            client_id=client.id,
            membership_id=assigned_membership.id,
            assigned_by=owner.id,
        ))
        run = ReconciliationRun(
            workspace_id=workspace.id,
            client_id=client.id,
            client_gstin_id=gstin.id,
            created_by=owner.id,
        )
        db.add(run)

        outsider = person(db, "outsider")
        other_workspace = Workspace(name="Outside", type="FIRM", created_by=outsider.id)
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
            gstin="08AAAAA0000A1Z3",
        )
        db.add(outsider_gstin)
        db.commit()

    return Graph(
        owner, admin, assigned, unassigned, workspace, client, gstin, other_client, other_gstin,
        run, other_workspace, outsider, outsider_client, outsider_gstin,
    )


def create_profile(graph, actor=None, version="7.1"):
    return service.create_profile(
        actor or graph.owner,
        graph.workspace.id,
        graph.client.id,
        graph.gstin.id,
        company_name="Primary Client Books",
        tally_version=version,
        purchase_voucher_type="Purchase",
        debit_note_voucher_type="Debit Note",
        igst_ledger_name="Input IGST",
        cgst_ledger_name="Input CGST",
        sgst_ledger_name="Input SGST",
        numbering_strategy="TALLY_AUTO",
    )


def prepare_eligible_purchase(graph, schema_engine):
    profile = create_profile(graph)
    service.create_party_mapping(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id,
        supplier_gstin="03DEMOX1234A1Z5", source_supplier_name="Supplier Private Limited",
        tally_party_ledger_name="Supplier Ledger",
    )
    service.create_purchase_ledger_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
        transaction_kind="purchase_invoice", gst_rate="18",
        tally_purchase_ledger_name="Purchases 18%",
    )
    source = ParsedInvoiceRecord(
        source=RecordSource.GST, source_sheet="b2b", source_row=2,
        sheet_category=SheetCategory.B2B, supplier_name="Supplier Private Limited",
        gstin_original="03DEMOX1234A1Z5", gstin_normalized="03DEMOX1234A1Z5",
        gstin_valid=True, invoice_number=normalize_invoice_number("INV-1"),
        invoice_date=date(2026, 4, 1), voucher_type=VoucherType.PURCHASE,
        taxable_value=Decimal("100"), cgst=Decimal("9"), sgst=Decimal("9"),
        gst_rate=Decimal("18"), place_of_supply="Haryana", reverse_charge="No",
    )
    candidate = purchase_candidate(aggregate_gst_invoices([source])[0])
    validation = validation_service.validate_persisted_tally_candidate(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, candidate,
    )
    assert validation.eligible
    with Session(schema_engine) as db:
        run = db.get(ReconciliationRun, graph.run.id)
        run.status = "COMPLETED"
        run.completed_at = utcnow()
        run.result_version = 1
        db.add(run)
        db.commit()
    return profile, candidate, validation


def authoritative_purchase_summary():
    source = ParsedInvoiceRecord(
        source=RecordSource.GST, source_sheet="b2b", source_row=2,
        sheet_category=SheetCategory.B2B, supplier_name="Supplier Private Limited",
        gstin_original="03DEMOX1234A1Z5", gstin_normalized="03DEMOX1234A1Z5",
        gstin_valid=True, invoice_number=normalize_invoice_number("INV-1"),
        invoice_date=date(2026, 4, 1), voucher_type=VoucherType.PURCHASE,
        taxable_value=Decimal("100"), cgst=Decimal("9"), sgst=Decimal("9"),
        gst_rate=Decimal("18"), place_of_supply="Haryana", reverse_charge="No",
    )
    return ReconciliationSummary(not_in_tally_records=[aggregate_gst_invoices([source])[0]])


def test_supported_profile_version_and_audit(graph, schema_engine):
    result = create_profile(graph)
    assert result["tally_version"] == "7.1" and result["version"] == 1
    with Session(schema_engine) as db:
        event = db.exec(select(AuditEvent).where(
            AuditEvent.entity_id == result["id"],
            AuditEvent.action == "TALLY_EXPORT_PROFILE_CREATED",
        )).one()
        assert event.client_id == graph.client.id
        assert event.event_metadata == {"version": 1, "status": "ACTIVE"}


@pytest.mark.parametrize("version", ["7.0", "6.6"])
def test_unsupported_profile_versions_are_rejected(graph, version):
    with pytest.raises(HTTPException) as invalid:
        create_profile(graph, version=version)
    assert invalid.value.status_code == 422
    assert invalid.value.detail == "Tally Export V1 supports TallyPrime 7.1 only"


def test_profile_optimistic_conflict(graph):
    profile = create_profile(graph)
    updated = service.update_profile(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
        expected_version=profile["version"], company_name="Updated Books", tally_version="7.1",
        purchase_voucher_type="Purchase", debit_note_voucher_type="Debit Note",
        igst_ledger_name="Input IGST", cgst_ledger_name="Input CGST",
        sgst_ledger_name="Input SGST", numbering_strategy="TALLY_AUTO", status="ACTIVE",
    )
    assert updated["version"] == 2
    with pytest.raises(HTTPException) as stale:
        service.update_profile(
            graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
            expected_version=1, company_name="Stale", tally_version="7.1",
            purchase_voucher_type="Purchase", debit_note_voucher_type=None,
            igst_ledger_name="Input IGST", cgst_ledger_name="Input CGST",
            sgst_ledger_name="Input SGST", numbering_strategy="TALLY_AUTO", status="ACTIVE",
        )
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as unsupported_numbering:
        service.update_profile(
            graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
            expected_version=updated["version"], company_name="Updated Books", tally_version="7.1",
            purchase_voucher_type="Purchase", debit_note_voucher_type="Debit Note",
            igst_ledger_name="Input IGST", cgst_ledger_name="Input CGST",
            sgst_ledger_name="Input SGST", numbering_strategy="ALIGNED_GENERATED", status="ACTIVE",
        )
    assert unsupported_numbering.value.status_code == 422


def test_authorization_and_tenant_isolation(graph):
    assert create_profile(graph, graph.admin)["client_id"] == graph.client.id
    assert service.get_profile(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id
    )["client_id"] == graph.client.id
    with pytest.raises(HTTPException) as assigned_profile_denied:
        service.update_profile(
            graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id,
            expected_version=1, company_name="Denied", tally_version="7.1",
            purchase_voucher_type="Purchase", debit_note_voucher_type=None,
            igst_ledger_name="Input IGST", cgst_ledger_name="Input CGST",
            sgst_ledger_name="Input SGST", numbering_strategy="TALLY_AUTO", status="ACTIVE",
        )
    assert assigned_profile_denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        service.get_profile(graph.unassigned, graph.workspace.id, graph.client.id, graph.gstin.id)
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_gstin:
        service.get_profile(graph.owner, graph.workspace.id, graph.client.id, graph.other_gstin.id)
    assert wrong_gstin.value.status_code == 404
    with pytest.raises(HTTPException) as cross_workspace:
        service.get_profile(
            graph.outsider, graph.other_workspace.id, graph.outsider_client.id, graph.gstin.id
        )
    assert cross_workspace.value.status_code == 404


def test_party_mapping_exact_gstin_uniqueness_name_updates_and_deactivation(graph, schema_engine):
    create_profile(graph)
    first = service.create_party_mapping(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id,
        supplier_gstin="03aaack5657l1ze",
        source_supplier_name="Aviraj Advisory Private Limited",
        tally_party_ledger_name="Aviraj Advisory",
    )
    assert service.resolve_party_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id, "03DEMOX1234A1Z5"
    )["id"] == first["id"]
    with pytest.raises(HTTPException) as duplicate:
        service.create_party_mapping(
            graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
            supplier_gstin="03DEMOX1234A1Z5", source_supplier_name="Changed source",
            tally_party_ledger_name="Other ledger",
        )
    assert duplicate.value.status_code == 409

    observed = service.update_party_mapping(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id,
        supplier_gstin="03DEMOX1234A1Z5", expected_version=first["version"],
        source_supplier_name="Aviraj Advisory Pvt Ltd",
    )
    assert observed["last_source_supplier_name"] == "Aviraj Advisory Pvt Ltd"
    assert observed["tally_party_ledger_name"] == "Aviraj Advisory"
    inactive = service.deactivate_party_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
        supplier_gstin="03DEMOX1234A1Z5", expected_version=observed["version"],
    )
    assert inactive["status"] == "INACTIVE"
    assert service.resolve_party_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id, "03DEMOX1234A1Z5"
    ) is None
    with Session(schema_engine) as db:
        actions = db.exec(select(AuditEvent.action).where(
            AuditEvent.entity_id == first["id"],
        ).order_by(AuditEvent.created_at)).all()
        assert actions == [
            "TALLY_PARTY_MAPPING_CREATED",
            "TALLY_PARTY_MAPPING_UPDATED",
            "TALLY_PARTY_MAPPING_DEACTIVATED",
        ]


def test_purchase_mapping_rate_lookup_and_multi_rate_readiness(graph):
    create_profile(graph)
    mappings = [
        service.create_purchase_ledger_mapping(
            graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
            transaction_kind="purchase_invoice", gst_rate=rate,
            tally_purchase_ledger_name=f"Purchase {rate}%",
        )
        for rate in ("0", "5", "12", "18")
    ]
    assert [row["gst_rate"] for row in service.list_purchase_ledger_mappings(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id
    )] == [Decimal("0.000"), Decimal("5.000"), Decimal("12.000"), Decimal("18.000")]
    resolved = service.resolve_purchase_ledger_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
        transaction_kind="purchase_invoice", gst_rate="18.000",
    )
    assert resolved["id"] == mappings[-1]["id"]
    updated = service.update_purchase_ledger_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
        transaction_kind="purchase_invoice", gst_rate=18, expected_version=1,
        tally_purchase_ledger_name="Purchases @ 18%", status="INACTIVE",
    )
    assert updated["version"] == 2
    assert service.resolve_purchase_ledger_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
        transaction_kind="purchase_invoice", gst_rate=18,
    ) is None


def test_assigned_accountant_cannot_change_purchase_ledger_configuration(graph):
    create_profile(graph)
    with pytest.raises(HTTPException) as denied:
        service.create_purchase_ledger_mapping(
            graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id,
            transaction_kind="purchase_invoice", gst_rate="18",
            tally_purchase_ledger_name="Purchases 18%",
        )
    assert denied.value.status_code == 403


def test_schema_indexes_constraints_and_cross_client_profile_rejected(graph, schema_engine):
    inspector = inspect(schema_engine)
    assert {
        "tally_export_profiles", "tally_party_ledger_mappings",
        "tally_purchase_ledger_mappings", "tally_export_batches", "tally_export_items",
        "tally_export_candidate_snapshots",
    }.issubset(inspector.get_table_names())
    assert "ix_tally_party_mappings_exact_lookup" in {
        row["name"] for row in inspector.get_indexes("tally_party_ledger_mappings")
    }
    assert "ix_tally_purchase_mappings_rate_lookup" in {
        row["name"] for row in inspector.get_indexes("tally_purchase_ledger_mappings")
    }
    assert "ix_tally_candidate_snapshots_run_version" in {
        row["name"] for row in inspector.get_indexes("tally_export_candidate_snapshots")
    }
    with Session(schema_engine) as db:
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(TallyExportProfile(
                    workspace_id=graph.workspace.id,
                    client_id=graph.client.id,
                    client_gstin_id=graph.other_gstin.id,
                    company_name="Cross client",
                    tally_version="7.1",
                    purchase_voucher_type="Purchase",
                    igst_ledger_name="IGST",
                    cgst_ledger_name="CGST",
                    sgst_ledger_name="SGST",
                    numbering_strategy="TALLY_AUTO",
                    created_by=graph.owner.id,
                    updated_by=graph.owner.id,
                ))
                db.flush()


def test_runtime_role_has_only_required_tally_table_writes(schema_engine):
    role = os.getenv("DATABASE_ROLE", "").strip()
    if not role:
        pytest.skip("Set DATABASE_ROLE to verify migration grants for an application role")
    with schema_engine.connect() as connection:
        for table in (
            "tally_export_profiles", "tally_party_ledger_mappings",
            "tally_purchase_ledger_mappings", "tally_export_batches", "tally_export_items",
        ):
            assert connection.scalar(text(
                "SELECT has_table_privilege(:role, :table, 'SELECT,INSERT,UPDATE')"
            ), {"role": role, "table": table})
            assert not connection.scalar(text(
                "SELECT has_table_privilege(:role, :table, 'DELETE')"
            ), {"role": role, "table": table})
        assert connection.scalar(text(
            "SELECT has_table_privilege(:role, 'tally_export_candidate_snapshots', 'SELECT,INSERT')"
        ), {"role": role})
        assert not connection.scalar(text(
            "SELECT has_table_privilege(:role, 'tally_export_candidate_snapshots', 'UPDATE')"
        ), {"role": role})
        assert not connection.scalar(text(
            "SELECT has_table_privilege(:role, 'tally_export_candidate_snapshots', 'DELETE')"
        ), {"role": role})


def test_export_batch_and_item_foundation_and_tenant_safe_previous_link(graph, schema_engine):
    profile = create_profile(graph)
    with Session(schema_engine, expire_on_commit=False) as db:
        batch = TallyExportBatch(
            workspace_id=graph.workspace.id,
            client_id=graph.client.id,
            client_gstin_id=graph.gstin.id,
            reconciliation_run_id=graph.run.id,
            profile_id=profile["id"],
            profile_version=profile["version"],
            tally_version=profile["tally_version"],
            selected_count=2,
            created_by=graph.owner.id,
        )
        db.add(batch)
        db.flush()
        first = TallyExportItem(
            workspace_id=graph.workspace.id,
            client_id=graph.client.id,
            client_gstin_id=graph.gstin.id,
            batch_id=batch.id,
            candidate_id="invoice:03DEMOX1234A1Z5:INV-1",
            source_identity_json={"rows": [{"sheet": "b2b", "row": 2}]},
            transaction_fingerprint="a" * 64,
            remote_id=uuid4(),
            document_kind="purchase_invoice",
            outcome="SELECTED",
            blocker_codes=[],
            warning_codes=["PARTY_MAPPING_REQUIRED"],
        )
        db.add(first)
        db.flush()
        second = TallyExportItem(
            workspace_id=graph.workspace.id,
            client_id=graph.client.id,
            client_gstin_id=graph.gstin.id,
            batch_id=batch.id,
            candidate_id="invoice:03DEMOX1234A1Z5:INV-2",
            transaction_fingerprint="b" * 64,
            remote_id=uuid4(),
            document_kind="purchase_invoice",
            outcome="BLOCKED",
            blocker_codes=["CESS_UNSUPPORTED"],
            warning_codes=[],
            previous_export_item_id=first.id,
        )
        db.add(second)
        db.commit()
        assert db.get(TallyExportBatch, batch.id).selected_count == 2
        assert db.get(TallyExportItem, second.id).previous_export_item_id == first.id

        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(TallyExportItem(
                    workspace_id=graph.workspace.id,
                    client_id=graph.client.id,
                    client_gstin_id=graph.other_gstin.id,
                    batch_id=batch.id,
                    candidate_id="cross-client",
                    transaction_fingerprint="c" * 64,
                    remote_id=uuid4(),
                    document_kind="purchase_invoice",
                    outcome="SELECTED",
                ))
                db.flush()


def test_persisted_validator_is_available_to_assigned_accountant_and_detects_previous_export(
    graph, schema_engine,
):
    profile_row = create_profile(graph)
    service.create_party_mapping(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id,
        supplier_gstin="03DEMOX1234A1Z5", source_supplier_name="Supplier Private Limited",
        tally_party_ledger_name="Supplier Ledger",
    )
    service.create_purchase_ledger_mapping(
        graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id,
        transaction_kind="purchase_invoice", gst_rate="18",
        tally_purchase_ledger_name="Purchases 18%",
    )
    source = ParsedInvoiceRecord(
        source=RecordSource.GST, source_sheet="b2b", source_row=2,
        sheet_category=SheetCategory.B2B, supplier_name="Supplier Private Limited",
        gstin_original="03DEMOX1234A1Z5", gstin_normalized="03DEMOX1234A1Z5",
        gstin_valid=True, invoice_number=normalize_invoice_number("INV-1"),
        invoice_date=date(2026, 4, 1), voucher_type=VoucherType.PURCHASE,
        taxable_value=Decimal("100"), cgst=Decimal("9"), sgst=Decimal("9"),
        gst_rate=Decimal("18"), place_of_supply="Haryana", reverse_charge="No",
    )
    tally_candidate = purchase_candidate(aggregate_gst_invoices([source])[0])
    first = validation_service.validate_persisted_tally_candidate(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, tally_candidate,
    )
    assert first.eligible

    with Session(schema_engine) as db:
        batch = TallyExportBatch(
            workspace_id=graph.workspace.id, client_id=graph.client.id,
            client_gstin_id=graph.gstin.id, reconciliation_run_id=graph.run.id,
            profile_id=profile_row["id"], profile_version=profile_row["version"],
            tally_version=profile_row["tally_version"], selected_count=1,
            exported_count=1, created_by=graph.owner.id,
        )
        db.add(batch)
        db.flush()
        db.add(TallyExportItem(
            workspace_id=graph.workspace.id, client_id=graph.client.id,
            client_gstin_id=graph.gstin.id, batch_id=batch.id,
            candidate_id=first.candidate_id, transaction_fingerprint=first.fingerprint,
            remote_id=first.remote_id, document_kind="purchase_invoice", outcome="EXPORTED",
        ))
        db.commit()

    repeated = validation_service.validate_persisted_tally_candidate(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, tally_candidate,
    )
    assert repeated.eligible
    assert TallyWarningCode.PREVIOUSLY_EXPORTED.value in {issue.code for issue in repeated.warnings}


def test_authoritative_candidate_snapshot_is_durable_reconstructable_and_tenant_scoped(
    graph, schema_engine,
):
    summary = authoritative_purchase_summary()
    expected = purchase_candidate(summary.not_in_tally_records[0])
    with Session(schema_engine) as db:
        run = db.get(ReconciliationRun, graph.run.id)
        run.status = "COMPLETED"
        run.completed_at = utcnow()
        run.result_version = 1
        db.add(run)
        assert candidate_service.persist_authoritative_purchase_candidates(db, run, summary) == 1
        db.commit()

    with Session(schema_engine) as db:
        row = db.exec(select(TallyExportCandidateSnapshot).where(
            TallyExportCandidateSnapshot.reconciliation_run_id == graph.run.id,
        )).one()
        run = db.get(ReconciliationRun, graph.run.id)
        assert (row.workspace_id, row.client_id, row.client_gstin_id) == (
            graph.workspace.id, graph.client.id, graph.gstin.id,
        )
        assert row.result_version == run.result_version == run.tally_candidate_snapshot_version == 1
        assert row.tax_structure == "cgst_sgst"

    version, reconstructed = candidate_service.load_durable_purchase_candidates(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
    )
    assert version == 1
    assert reconstructed == (expected,)

    with pytest.raises(HTTPException) as denied:
        candidate_service.load_durable_purchase_candidates(
            graph.outsider, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
        )
    assert denied.value.status_code == 404


def test_completed_legacy_run_without_candidate_snapshot_fails_explicitly(graph, schema_engine):
    with Session(schema_engine) as db:
        run = db.get(ReconciliationRun, graph.run.id)
        run.status = "COMPLETED"
        run.completed_at = utcnow()
        run.result_version = 1
        db.add(run)
        db.commit()

    with pytest.raises(candidate_service.TallyCandidateSnapshotUnavailable, match="rerun reconciliation"):
        candidate_service.load_durable_purchase_candidates(
            graph.owner, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
        )


def test_generated_batch_persists_xml_manifest_identity_and_explicit_regeneration(
    graph, schema_engine,
):
    profile, candidate, validation = prepare_eligible_purchase(graph, schema_engine)
    written = {}

    def writer(batch_id, xml_bytes):
        written[batch_id] = xml_bytes
        return f"private://tally/{batch_id}.xml"

    operation_key = uuid4()
    first = batch_service.generate_and_persist_tally_batch(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
        [(candidate, validation)], operation_key=operation_key, artifact_writer=writer,
    )
    assert first.artifact.manifest.profile_id == profile["id"]
    assert first.artifact.manifest.profile_version == profile["version"]
    assert first.artifact.manifest.candidate_ids == (validation.candidate_id,)
    assert first.artifact.manifest.remote_ids == (validation.remote_id,)
    assert first.artifact.manifest.fingerprints == (validation.fingerprint,)
    assert written[first.batch_id] == first.artifact.xml_bytes

    with Session(schema_engine) as db:
        batch = db.get(TallyExportBatch, first.batch_id)
        item = db.exec(select(TallyExportItem).where(
            TallyExportItem.batch_id == first.batch_id,
        )).one()
        assert batch.status == "GENERATED"
        assert batch.exported_count == 1
        assert batch.content_sha256 == first.artifact.content_sha256
        assert batch.artifact_object_ref == f"private://tally/{first.batch_id}.xml"
        assert item.outcome == "EXPORTED"
        assert item.remote_id == validation.remote_id
        assert item.transaction_fingerprint == validation.fingerprint
        assert item.display_snapshot_json == {
            "supplier_gstin": "03DEMOX1234A1Z5",
            "supplier_name": "Supplier Private Limited",
            "document_number": "INV-1",
            "document_date": "2026-04-01",
            "taxable_value": "100.00",
            "total_tax": "18.00",
            "invoice_total": None,
            "gst_rates": ["18"],
        }
        assert item.source_identity_json["rows"] == [{
            "source": "gst", "sheet": "b2b", "row": 2, "category": "b2b",
        }]

    retry = batch_service.generate_and_persist_tally_batch(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
        [(candidate, validation)], operation_key=operation_key, artifact_writer=writer,
    )
    assert retry.batch_id == first.batch_id
    assert retry.status == "GENERATED" and retry.reused
    assert retry.artifact is None
    assert len(written) == 1

    with pytest.raises(batch_service.TallyExportIdempotencyConflict, match="different"):
        batch_service.generate_and_persist_tally_batch(
            graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
            [(candidate, validation)], operation_key=operation_key, allow_reexport=True,
            artifact_writer=writer,
        )

    with pytest.raises(batch_service.TallyVoucherExportError, match="Explicit confirmation"):
        batch_service.generate_and_persist_tally_batch(
            graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
            [(candidate, validation)], operation_key=uuid4(), artifact_writer=writer,
        )

    repeated = batch_service.generate_and_persist_tally_batch(
        graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
        [(candidate, validation)], operation_key=uuid4(), allow_reexport=True,
        artifact_writer=writer,
    )
    assert repeated.artifact.content_sha256 == first.artifact.content_sha256
    with Session(schema_engine) as db:
        first_item = db.exec(select(TallyExportItem).where(
            TallyExportItem.batch_id == first.batch_id,
        )).one()
        repeated_item = db.exec(select(TallyExportItem).where(
            TallyExportItem.batch_id == repeated.batch_id,
        )).one()
        assert repeated_item.previous_export_item_id == first_item.id
        assert repeated_item.outcome == "EXPORTED"


def test_batch_generation_is_tenant_scoped(graph, schema_engine):
    _, candidate, validation = prepare_eligible_purchase(graph, schema_engine)
    with pytest.raises(HTTPException) as denied:
        batch_service.generate_and_persist_tally_batch(
            graph.outsider, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
            [(candidate, validation)], operation_key=uuid4(),
            artifact_writer=lambda *_: "unreachable",
        )
    assert denied.value.status_code == 404
    with Session(schema_engine) as db:
        assert db.exec(select(TallyExportBatch).where(
            TallyExportBatch.reconciliation_run_id == graph.run.id,
        )).all() == []


def test_private_storage_failure_persists_failed_batch_and_skipped_item(graph, schema_engine):
    _, candidate, validation = prepare_eligible_purchase(graph, schema_engine)

    def failing_writer(_batch_id, _xml_bytes):
        raise OSError(r"private storage unavailable at C:\private\secret\artifact.xml token=secret")

    with pytest.raises(batch_service.TallyExportPersistenceError, match="persistence failed"):
        batch_service.generate_and_persist_tally_batch(
            graph.assigned, graph.workspace.id, graph.client.id, graph.gstin.id, graph.run.id,
            [(candidate, validation)], operation_key=uuid4(), artifact_writer=failing_writer,
        )
    with Session(schema_engine) as db:
        batch = db.exec(select(TallyExportBatch).where(
            TallyExportBatch.reconciliation_run_id == graph.run.id,
        )).one()
        item = db.exec(select(TallyExportItem).where(
            TallyExportItem.batch_id == batch.id,
        )).one()
        assert batch.status == "FAILED"
        assert batch.exported_count == 0
        assert batch.artifact_object_ref is None and batch.content_sha256 is None
        assert batch.failure_code == "ARTIFACT_PERSISTENCE_FAILED"
        assert batch.failure_message == "Private XML artifact persistence failed"
        assert len(batch.failure_message) <= 500
        assert "secret" not in batch.failure_message.casefold()
        assert item.outcome == "SKIPPED"
        event = db.exec(select(AuditEvent).where(
            AuditEvent.entity_id == batch.id,
            AuditEvent.action == "TALLY_EXPORT_BATCH_FAILED",
        )).one()
        assert event.event_metadata["reason"] == "Private XML artifact persistence failed"
        assert event.event_metadata["failure_code"] == "ARTIFACT_PERSISTENCE_FAILED"
        assert "secret" not in str(event.event_metadata).casefold()
