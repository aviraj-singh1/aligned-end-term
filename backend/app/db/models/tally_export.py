"""Persistent configuration and history foundations for future Tally exports."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, Numeric, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .base import TimestampedModel


class TallyProfileStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class TallyNumberingStrategy(str, Enum):
    TALLY_AUTO = "TALLY_AUTO"
    ALIGNED_GENERATED = "ALIGNED_GENERATED"


class TallyExportBatchStatus(str, Enum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    FAILED = "FAILED"


class TallyExportItemOutcome(str, Enum):
    SELECTED = "SELECTED"
    EXPORTED = "EXPORTED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class TallyExportCandidateSnapshot(TimestampedModel, table=True):
    """Immutable, versioned Not-in-Tally purchase candidate evidence."""

    __tablename__ = "tally_export_candidate_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "reconciliation_run_id"],
            [
                "reconciliation_runs.workspace_id", "reconciliation_runs.client_id",
                "reconciliation_runs.client_gstin_id", "reconciliation_runs.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("reconciliation_run_id", "result_version", "candidate_id"),
        CheckConstraint("result_version >= 1", name="result_version"),
        CheckConstraint("transaction_type = 'purchase_invoice'", name="transaction_type"),
        CheckConstraint(
            "tax_structure IN ('no_tax', 'igst_only', 'cgst_sgst', 'mixed_or_inconsistent')",
            name="tax_structure",
        ),
        CheckConstraint("length(btrim(candidate_id)) > 0", name="candidate_id_not_blank"),
        CheckConstraint("transaction_fingerprint ~ '^[0-9a-f]{64}$'", name="transaction_fingerprint"),
        CheckConstraint("length(btrim(supplier_name)) > 0", name="supplier_name_not_blank"),
        CheckConstraint("length(btrim(document_number)) > 0", name="document_number_not_blank"),
        CheckConstraint(
            "jsonb_typeof(gst_rates_json) = 'array' AND octet_length(gst_rates_json::text) <= 4096",
            name="gst_rates_bounded_array",
        ),
        CheckConstraint(
            "jsonb_typeof(rate_allocations_json) = 'array' AND octet_length(rate_allocations_json::text) <= 16384",
            name="rate_alloc_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(place_of_supply_values_json) = 'array' AND octet_length(place_of_supply_values_json::text) <= 4096",
            name="place_values_bounded_array",
        ),
        CheckConstraint(
            "jsonb_typeof(reverse_charge_values_json) = 'array' AND octet_length(reverse_charge_values_json::text) <= 4096",
            name="rcm_values_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(cancellation_evidence_json) = 'array' AND octet_length(cancellation_evidence_json::text) <= 8192",
            name="cancellation_bounded_array",
        ),
        CheckConstraint(
            "jsonb_typeof(financial_parse_issues_json) = 'array' AND octet_length(financial_parse_issues_json::text) <= 16384",
            name="parse_issues_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(source_identity_json) = 'array' AND octet_length(source_identity_json::text) <= 65536",
            name="source_rows_bounded",
        ),
        Index(
            "ix_tally_candidate_snapshots_run_version",
            "workspace_id", "client_id", "client_gstin_id", "reconciliation_run_id", "result_version",
        ),
        Index("ix_tally_candidate_snapshots_remote_id", "remote_id"),
    )

    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    reconciliation_run_id: UUID
    result_version: int
    candidate_id: str = Field(max_length=255)
    remote_id: UUID
    transaction_fingerprint: str = Field(max_length=64)
    transaction_type: str = Field(max_length=40)
    tax_structure: str = Field(max_length=30)
    supplier_gstin: str = Field(max_length=15)
    supplier_gstin_valid: bool
    supplier_name: str = Field(max_length=1024)
    document_number: str = Field(max_length=1024)
    document_date: date | None = Field(default=None, sa_type=Date)
    original_invoice_number: str = Field(default="", max_length=1024)
    taxable_value: Decimal = Field(sa_type=Numeric(24, 6))
    igst: Decimal = Field(sa_type=Numeric(24, 6))
    cgst: Decimal = Field(sa_type=Numeric(24, 6))
    sgst: Decimal = Field(sa_type=Numeric(24, 6))
    cess: Decimal = Field(sa_type=Numeric(24, 6))
    cess_present: bool
    invoice_total: Decimal | None = Field(default=None, sa_type=Numeric(24, 6))
    gst_rates_json: list[str] = Field(sa_type=JSONB(none_as_null=True))
    rate_allocations_json: list[dict[str, str]] = Field(sa_type=JSONB(none_as_null=True))
    gst_rate_missing: bool
    place_of_supply: str = Field(default="", max_length=255)
    place_of_supply_values_json: list[str] = Field(sa_type=JSONB(none_as_null=True))
    place_of_supply_missing: bool
    place_of_supply_conflicting: bool
    reverse_charge_status: str = Field(max_length=20)
    reverse_charge_values_json: list[str] = Field(sa_type=JSONB(none_as_null=True))
    is_amendment: bool
    amendment_original_number: str = Field(default="", max_length=1024)
    amendment_original_date: date | None = Field(default=None, sa_type=Date)
    is_cancelled: bool
    cancellation_evidence_json: list[str] = Field(sa_type=JSONB(none_as_null=True))
    financial_parse_issues_json: list[dict[str, str]] = Field(sa_type=JSONB(none_as_null=True))
    source_identity_json: list[dict[str, Any]] = Field(sa_type=JSONB(none_as_null=True))


class TallyExportProfile(TimestampedModel, table=True):
    __tablename__ = "tally_export_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id"),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "id"),
        CheckConstraint("tally_version IN ('7.0', '7.1')", name="tally_version"),
        CheckConstraint("numbering_strategy IN ('TALLY_AUTO', 'ALIGNED_GENERATED')", name="numbering_strategy"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="status"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "length(btrim(company_name)) > 0 AND length(btrim(purchase_voucher_type)) > 0 "
            "AND length(btrim(igst_ledger_name)) > 0 AND length(btrim(cgst_ledger_name)) > 0 "
            "AND length(btrim(sgst_ledger_name)) > 0",
            name="required_names_not_blank",
        ),
        CheckConstraint(
            "debit_note_voucher_type IS NULL OR length(btrim(debit_note_voucher_type)) > 0",
            name="debit_note_type_not_blank",
        ),
        Index("ix_tally_export_profiles_lookup", "workspace_id", "client_id", "client_gstin_id", "status"),
        Index("ix_tally_export_profiles_created_by", "created_by"),
        Index("ix_tally_export_profiles_updated_by", "updated_by"),
    )

    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    company_name: str = Field(max_length=255)
    tally_version: str = Field(max_length=10)
    purchase_voucher_type: str = Field(max_length=200)
    debit_note_voucher_type: str | None = Field(default=None, max_length=200)
    igst_ledger_name: str = Field(max_length=255)
    cgst_ledger_name: str = Field(max_length=255)
    sgst_ledger_name: str = Field(max_length=255)
    numbering_strategy: str = Field(max_length=30)
    status: str = Field(default=TallyProfileStatus.ACTIVE.value, max_length=20,
                        sa_column_kwargs={"server_default": TallyProfileStatus.ACTIVE.value})
    version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    created_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    updated_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")


class TallyPartyLedgerMapping(TimestampedModel, table=True):
    __tablename__ = "tally_party_ledger_mappings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "supplier_gstin"),
        CheckConstraint(
            "supplier_gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$'",
            name="supplier_gstin_format",
        ),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="status"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "length(btrim(last_source_supplier_name)) > 0 AND length(btrim(tally_party_ledger_name)) > 0",
            name="names_not_blank",
        ),
        Index(
            "ix_tally_party_mappings_exact_lookup",
            "workspace_id", "client_id", "client_gstin_id", "supplier_gstin", "status",
        ),
        Index("ix_tally_party_mappings_confirmed_by", "confirmed_by"),
    )

    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    supplier_gstin: str = Field(max_length=15)
    last_source_supplier_name: str = Field(max_length=255)
    tally_party_ledger_name: str = Field(max_length=255)
    status: str = Field(default=TallyProfileStatus.ACTIVE.value, max_length=20,
                        sa_column_kwargs={"server_default": TallyProfileStatus.ACTIVE.value})
    confirmed_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    confirmed_at: datetime = Field(sa_type=DateTime(timezone=True))
    version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})


class TallyPurchaseLedgerMapping(TimestampedModel, table=True):
    __tablename__ = "tally_purchase_ledger_mappings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "profile_id"],
            [
                "tally_export_profiles.workspace_id", "tally_export_profiles.client_id",
                "tally_export_profiles.client_gstin_id", "tally_export_profiles.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "transaction_kind", "gst_rate"),
        CheckConstraint("transaction_kind IN ('purchase_invoice', 'supplier_credit_note')", name="transaction_kind"),
        CheckConstraint("gst_rate >= 0 AND gst_rate <= 100", name="gst_rate"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="status"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("length(btrim(tally_purchase_ledger_name)) > 0", name="ledger_name_not_blank"),
        Index(
            "ix_tally_purchase_mappings_rate_lookup",
            "workspace_id", "client_id", "client_gstin_id", "transaction_kind", "gst_rate", "status",
        ),
        Index("ix_tally_purchase_mappings_profile", "profile_id"),
    )

    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    profile_id: UUID
    transaction_kind: str = Field(max_length=40)
    gst_rate: Decimal = Field(sa_type=Numeric(7, 3))
    tally_purchase_ledger_name: str = Field(max_length=255)
    status: str = Field(default=TallyProfileStatus.ACTIVE.value, max_length=20,
                        sa_column_kwargs={"server_default": TallyProfileStatus.ACTIVE.value})
    version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    created_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    updated_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")


class TallyExportBatch(TimestampedModel, table=True):
    __tablename__ = "tally_export_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "reconciliation_run_id"],
            [
                "reconciliation_runs.workspace_id", "reconciliation_runs.client_id",
                "reconciliation_runs.client_gstin_id", "reconciliation_runs.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "profile_id"],
            [
                "tally_export_profiles.workspace_id", "tally_export_profiles.client_id",
                "tally_export_profiles.client_gstin_id", "tally_export_profiles.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "id"),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "created_by", "operation_key"),
        CheckConstraint("tally_version IN ('7.0', '7.1')", name="tally_version"),
        CheckConstraint("status IN ('PENDING', 'GENERATED', 'FAILED')", name="status"),
        CheckConstraint("profile_version >= 1", name="profile_version"),
        CheckConstraint(
            "selected_count >= 0 AND exported_count >= 0 AND blocked_count >= 0 AND warning_count >= 0 "
            "AND exported_count + blocked_count <= selected_count",
            name="counts",
        ),
        CheckConstraint("content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256"),
        CheckConstraint("request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$'", name="request_fingerprint"),
        CheckConstraint(
            "(operation_key IS NULL AND request_fingerprint IS NULL) OR "
            "(operation_key IS NOT NULL AND request_fingerprint IS NOT NULL)",
            name="idempotency_metadata",
        ),
        CheckConstraint(
            "failure_message IS NULL OR length(failure_message) <= 500",
            name="failure_message_bounded",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,99}$'",
            name="failure_code_format",
        ),
        CheckConstraint(
            "(failure_code IS NULL AND failure_message IS NULL) OR "
            "(failure_code IS NOT NULL AND failure_message IS NOT NULL)",
            name="failure_metadata",
        ),
        CheckConstraint(
            "(artifact_object_ref IS NULL AND content_sha256 IS NULL) OR "
            "(artifact_object_ref IS NOT NULL AND length(btrim(artifact_object_ref)) > 0 AND content_sha256 IS NOT NULL)",
            name="artifact_metadata",
        ),
        Index("ix_tally_export_batches_workspace_client_created", "workspace_id", "client_id", "created_at", "id"),
        Index("ix_tally_export_batches_run", "workspace_id", "client_id", "reconciliation_run_id"),
        Index("ix_tally_export_batches_profile", "profile_id", "profile_version"),
        Index("ix_tally_export_batches_operation", "workspace_id", "created_by", "operation_key"),
    )

    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    reconciliation_run_id: UUID
    profile_id: UUID
    profile_version: int
    tally_version: str = Field(max_length=10)
    status: str = Field(default=TallyExportBatchStatus.PENDING.value, max_length=20,
                        sa_column_kwargs={"server_default": TallyExportBatchStatus.PENDING.value})
    selected_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    exported_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    blocked_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    warning_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    artifact_object_ref: str | None = Field(default=None, max_length=1024)
    content_sha256: str | None = Field(default=None, max_length=64)
    operation_key: UUID | None = Field(default=None)
    request_fingerprint: str | None = Field(default=None, max_length=64)
    failure_code: str | None = Field(default=None, max_length=100)
    failure_message: str | None = Field(default=None, max_length=500)
    created_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")


class TallyExportItem(TimestampedModel, table=True):
    __tablename__ = "tally_export_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "batch_id"],
            [
                "tally_export_batches.workspace_id", "tally_export_batches.client_id",
                "tally_export_batches.client_gstin_id", "tally_export_batches.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "previous_export_item_id"],
            [
                "tally_export_items.workspace_id", "tally_export_items.client_id",
                "tally_export_items.client_gstin_id", "tally_export_items.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "id"),
        UniqueConstraint("batch_id", "transaction_fingerprint"),
        CheckConstraint("length(btrim(candidate_id)) > 0", name="candidate_id_not_blank"),
        CheckConstraint("transaction_fingerprint ~ '^[0-9a-f]{64}$'", name="transaction_fingerprint"),
        CheckConstraint("document_kind IN ('purchase_invoice', 'supplier_credit_note')", name="document_kind"),
        CheckConstraint("outcome IN ('SELECTED', 'EXPORTED', 'BLOCKED', 'SKIPPED')", name="outcome"),
        CheckConstraint("previous_export_item_id IS NULL OR previous_export_item_id <> id", name="previous_not_self"),
        CheckConstraint(
            "source_identity_json IS NULL OR "
            "(jsonb_typeof(source_identity_json) = 'object' AND octet_length(source_identity_json::text) <= 16384)",
            name="source_identity_bounded_object",
        ),
        CheckConstraint(
            "jsonb_typeof(blocker_codes) = 'array' AND octet_length(blocker_codes::text) <= 8192",
            name="blocker_codes_bounded_array",
        ),
        CheckConstraint(
            "jsonb_typeof(warning_codes) = 'array' AND octet_length(warning_codes::text) <= 8192",
            name="warning_codes_bounded_array",
        ),
        CheckConstraint(
            "display_snapshot_json IS NULL OR "
            "(jsonb_typeof(display_snapshot_json) = 'object' AND octet_length(display_snapshot_json::text) <= 8192)",
            name="display_snapshot_bounded_object",
        ),
        Index("ix_tally_export_items_batch_outcome", "batch_id", "outcome"),
        Index(
            "ix_tally_export_items_target_fingerprint",
            "workspace_id", "client_id", "client_gstin_id", "transaction_fingerprint",
        ),
        Index("ix_tally_export_items_remote_id", "remote_id"),
    )

    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    batch_id: UUID
    candidate_id: str = Field(max_length=255)
    source_identity_json: dict[str, Any] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    transaction_fingerprint: str = Field(max_length=64)
    remote_id: UUID
    document_kind: str = Field(max_length=40)
    outcome: str = Field(max_length=20)
    blocker_codes: list[str] = Field(default_factory=list, sa_type=JSONB(none_as_null=True),
                                     sa_column_kwargs={"server_default": text("'[]'::jsonb")})
    warning_codes: list[str] = Field(default_factory=list, sa_type=JSONB(none_as_null=True),
                                     sa_column_kwargs={"server_default": text("'[]'::jsonb")})
    display_snapshot_json: dict[str, Any] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    previous_export_item_id: UUID | None = Field(default=None)
