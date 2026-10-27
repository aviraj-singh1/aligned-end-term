"""Add tenant-scoped Tally export configuration and history foundations."""

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260923_13"
down_revision = "20260914_12"
branch_labels = depends_on = None


def _application_role():
    explicit = os.getenv("DATABASE_ROLE", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("DATABASE_URL", "").strip()
    return make_url(raw).username if raw else None


def _tenant_client_fks(table):
    return (
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"],
            name=op.f(f"fk_{table}_workspace_id_client_id_clients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            name=op.f(f"fk_{table}_workspace_id_client_id_client_gstin_id_client_gstins"),
            ondelete="RESTRICT",
        ),
    )


def _timestamps():
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade():
    op.create_unique_constraint(
        op.f("uq_reconciliation_runs_workspace_id_client_id_client_gstin_id_id"),
        "reconciliation_runs", ["workspace_id", "client_id", "client_gstin_id", "id"],
    )

    op.create_table(
        "tally_export_profiles",
        *_timestamps(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("client_gstin_id", sa.Uuid(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("tally_version", sa.String(length=10), nullable=False),
        sa.Column("purchase_voucher_type", sa.String(length=200), nullable=False),
        sa.Column("debit_note_voucher_type", sa.String(length=200), nullable=True),
        sa.Column("igst_ledger_name", sa.String(length=255), nullable=False),
        sa.Column("cgst_ledger_name", sa.String(length=255), nullable=False),
        sa.Column("sgst_ledger_name", sa.String(length=255), nullable=False),
        sa.Column("numbering_strategy", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("tally_version IN ('7.0', '7.1')", name=op.f("ck_tally_export_profiles_tally_version")),
        sa.CheckConstraint(
            "numbering_strategy IN ('TALLY_AUTO', 'ALIGNED_GENERATED')",
            name=op.f("ck_tally_export_profiles_numbering_strategy"),
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name=op.f("ck_tally_export_profiles_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_tally_export_profiles_version")),
        sa.CheckConstraint(
            "length(btrim(company_name)) > 0 AND length(btrim(purchase_voucher_type)) > 0 "
            "AND length(btrim(igst_ledger_name)) > 0 AND length(btrim(cgst_ledger_name)) > 0 "
            "AND length(btrim(sgst_ledger_name)) > 0",
            name=op.f("ck_tally_export_profiles_required_names_not_blank"),
        ),
        sa.CheckConstraint(
            "debit_note_voucher_type IS NULL OR length(btrim(debit_note_voucher_type)) > 0",
            name=op.f("ck_tally_export_profiles_debit_note_type_not_blank"),
        ),
        *_tenant_client_fks("tally_export_profiles"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_tally_export_profiles_created_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name=op.f("fk_tally_export_profiles_updated_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_export_profiles")),
        sa.UniqueConstraint("workspace_id", "client_id", "client_gstin_id", name=op.f("uq_tally_export_profiles_workspace_id_client_id_client_gstin_id")),
        sa.UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "id", name=op.f("uq_tally_export_profiles_workspace_id_client_id_client_gstin_id_id")),
    )
    op.create_index("ix_tally_export_profiles_lookup", "tally_export_profiles", ["workspace_id", "client_id", "client_gstin_id", "status"])
    op.create_index("ix_tally_export_profiles_created_by", "tally_export_profiles", ["created_by"])
    op.create_index("ix_tally_export_profiles_updated_by", "tally_export_profiles", ["updated_by"])

    op.create_table(
        "tally_party_ledger_mappings",
        *_timestamps(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("client_gstin_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_gstin", sa.String(length=15), nullable=False),
        sa.Column("last_source_supplier_name", sa.String(length=255), nullable=False),
        sa.Column("tally_party_ledger_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "supplier_gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$'",
            name=op.f("ck_tally_party_ledger_mappings_supplier_gstin_format"),
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name=op.f("ck_tally_party_ledger_mappings_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_tally_party_ledger_mappings_version")),
        sa.CheckConstraint(
            "length(btrim(last_source_supplier_name)) > 0 AND length(btrim(tally_party_ledger_name)) > 0",
            name=op.f("ck_tally_party_ledger_mappings_names_not_blank"),
        ),
        *_tenant_client_fks("tally_party_ledger_mappings"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], name=op.f("fk_tally_party_ledger_mappings_confirmed_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_party_ledger_mappings")),
        sa.UniqueConstraint(
            "workspace_id", "client_id", "client_gstin_id", "supplier_gstin",
            name=op.f("uq_tally_party_ledger_mappings_workspace_id_client_id_client_gstin_id_supplier_gstin"),
        ),
    )
    op.create_index("ix_tally_party_mappings_exact_lookup", "tally_party_ledger_mappings", ["workspace_id", "client_id", "client_gstin_id", "supplier_gstin", "status"])
    op.create_index("ix_tally_party_mappings_confirmed_by", "tally_party_ledger_mappings", ["confirmed_by"])

    op.create_table(
        "tally_purchase_ledger_mappings",
        *_timestamps(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("client_gstin_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_kind", sa.String(length=40), nullable=False),
        sa.Column("gst_rate", sa.Numeric(precision=7, scale=3), nullable=False),
        sa.Column("tally_purchase_ledger_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("transaction_kind IN ('purchase_invoice', 'supplier_credit_note')", name=op.f("ck_tally_purchase_ledger_mappings_transaction_kind")),
        sa.CheckConstraint("gst_rate >= 0 AND gst_rate <= 100", name=op.f("ck_tally_purchase_ledger_mappings_gst_rate")),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name=op.f("ck_tally_purchase_ledger_mappings_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_tally_purchase_ledger_mappings_version")),
        sa.CheckConstraint("length(btrim(tally_purchase_ledger_name)) > 0", name=op.f("ck_tally_purchase_ledger_mappings_ledger_name_not_blank")),
        *_tenant_client_fks("tally_purchase_ledger_mappings"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "profile_id"],
            ["tally_export_profiles.workspace_id", "tally_export_profiles.client_id", "tally_export_profiles.client_gstin_id", "tally_export_profiles.id"],
            name=op.f("fk_tally_purchase_ledger_mappings_workspace_id_client_id_client_gstin_id_profile_id_tally_export_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_tally_purchase_ledger_mappings_created_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name=op.f("fk_tally_purchase_ledger_mappings_updated_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_purchase_ledger_mappings")),
        sa.UniqueConstraint(
            "workspace_id", "client_id", "client_gstin_id", "transaction_kind", "gst_rate",
            name=op.f("uq_tally_purchase_ledger_mappings_workspace_id_client_id_client_gstin_id_transaction_kind_gst_rate"),
        ),
    )
    op.create_index("ix_tally_purchase_mappings_rate_lookup", "tally_purchase_ledger_mappings", ["workspace_id", "client_id", "client_gstin_id", "transaction_kind", "gst_rate", "status"])
    op.create_index("ix_tally_purchase_mappings_profile", "tally_purchase_ledger_mappings", ["profile_id"])

    op.create_table(
        "tally_export_batches",
        *_timestamps(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("client_gstin_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_run_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("tally_version", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("selected_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("exported_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocked_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("artifact_object_ref", sa.String(length=1024), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("tally_version IN ('7.0', '7.1')", name=op.f("ck_tally_export_batches_tally_version")),
        sa.CheckConstraint("status IN ('PENDING', 'GENERATED', 'FAILED')", name=op.f("ck_tally_export_batches_status")),
        sa.CheckConstraint("profile_version >= 1", name=op.f("ck_tally_export_batches_profile_version")),
        sa.CheckConstraint(
            "selected_count >= 0 AND exported_count >= 0 AND blocked_count >= 0 AND warning_count >= 0 "
            "AND exported_count + blocked_count <= selected_count",
            name=op.f("ck_tally_export_batches_counts"),
        ),
        sa.CheckConstraint("content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_tally_export_batches_content_sha256")),
        sa.CheckConstraint(
            "(artifact_object_ref IS NULL AND content_sha256 IS NULL) OR "
            "(artifact_object_ref IS NOT NULL AND length(btrim(artifact_object_ref)) > 0 AND content_sha256 IS NOT NULL)",
            name=op.f("ck_tally_export_batches_artifact_metadata"),
        ),
        *_tenant_client_fks("tally_export_batches"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "reconciliation_run_id"],
            ["reconciliation_runs.workspace_id", "reconciliation_runs.client_id", "reconciliation_runs.client_gstin_id", "reconciliation_runs.id"],
            name=op.f("fk_tally_export_batches_workspace_id_client_id_client_gstin_id_reconciliation_run_id_reconciliation_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "profile_id"],
            ["tally_export_profiles.workspace_id", "tally_export_profiles.client_id", "tally_export_profiles.client_gstin_id", "tally_export_profiles.id"],
            name=op.f("fk_tally_export_batches_workspace_id_client_id_client_gstin_id_profile_id_tally_export_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_tally_export_batches_created_by_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_export_batches")),
        sa.UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "id", name=op.f("uq_tally_export_batches_workspace_id_client_id_client_gstin_id_id")),
    )
    op.create_index("ix_tally_export_batches_workspace_client_created", "tally_export_batches", ["workspace_id", "client_id", "created_at", "id"])
    op.create_index("ix_tally_export_batches_run", "tally_export_batches", ["workspace_id", "client_id", "reconciliation_run_id"])
    op.create_index("ix_tally_export_batches_profile", "tally_export_batches", ["profile_id", "profile_version"])

    op.create_table(
        "tally_export_items",
        *_timestamps(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("client_gstin_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.String(length=255), nullable=False),
        sa.Column("source_identity_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("transaction_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("remote_id", sa.Uuid(), nullable=False),
        sa.Column("document_kind", sa.String(length=40), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("blocker_codes", postgresql.JSONB(none_as_null=True), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("warning_codes", postgresql.JSONB(none_as_null=True), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("previous_export_item_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("length(btrim(candidate_id)) > 0", name=op.f("ck_tally_export_items_candidate_id_not_blank")),
        sa.CheckConstraint("transaction_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_tally_export_items_transaction_fingerprint")),
        sa.CheckConstraint("document_kind IN ('purchase_invoice', 'supplier_credit_note')", name=op.f("ck_tally_export_items_document_kind")),
        sa.CheckConstraint("outcome IN ('SELECTED', 'EXPORTED', 'BLOCKED', 'SKIPPED')", name=op.f("ck_tally_export_items_outcome")),
        sa.CheckConstraint("previous_export_item_id IS NULL OR previous_export_item_id <> id", name=op.f("ck_tally_export_items_previous_not_self")),
        sa.CheckConstraint(
            "source_identity_json IS NULL OR (jsonb_typeof(source_identity_json) = 'object' "
            "AND octet_length(source_identity_json::text) <= 16384)",
            name=op.f("ck_tally_export_items_source_identity_bounded_object"),
        ),
        sa.CheckConstraint("jsonb_typeof(blocker_codes) = 'array' AND octet_length(blocker_codes::text) <= 8192", name=op.f("ck_tally_export_items_blocker_codes_bounded_array")),
        sa.CheckConstraint("jsonb_typeof(warning_codes) = 'array' AND octet_length(warning_codes::text) <= 8192", name=op.f("ck_tally_export_items_warning_codes_bounded_array")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "batch_id"],
            ["tally_export_batches.workspace_id", "tally_export_batches.client_id", "tally_export_batches.client_gstin_id", "tally_export_batches.id"],
            name=op.f("fk_tally_export_items_workspace_id_client_id_client_gstin_id_batch_id_tally_export_batches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "previous_export_item_id"],
            ["tally_export_items.workspace_id", "tally_export_items.client_id", "tally_export_items.client_gstin_id", "tally_export_items.id"],
            name=op.f("fk_tally_export_items_workspace_id_client_id_client_gstin_id_previous_export_item_id_tally_export_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_export_items")),
        sa.UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "id", name=op.f("uq_tally_export_items_workspace_id_client_id_client_gstin_id_id")),
        sa.UniqueConstraint("batch_id", "transaction_fingerprint", name=op.f("uq_tally_export_items_batch_id_transaction_fingerprint")),
    )
    op.create_index("ix_tally_export_items_batch_outcome", "tally_export_items", ["batch_id", "outcome"])
    op.create_index("ix_tally_export_items_target_fingerprint", "tally_export_items", ["workspace_id", "client_id", "client_gstin_id", "transaction_fingerprint"])
    op.create_index("ix_tally_export_items_remote_id", "tally_export_items", ["remote_id"])

    role = _application_role()
    if role:
        prepare = op.get_bind().dialect.identifier_preparer
        for table in (
            "tally_export_profiles", "tally_party_ledger_mappings", "tally_purchase_ledger_mappings",
            "tally_export_batches", "tally_export_items",
        ):
            op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE {prepare.quote(table)} TO {prepare.quote(role)}")


def downgrade():
    role = _application_role()
    if role:
        prepare = op.get_bind().dialect.identifier_preparer
        for table in (
            "tally_export_items", "tally_export_batches", "tally_purchase_ledger_mappings",
            "tally_party_ledger_mappings", "tally_export_profiles",
        ):
            op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {prepare.quote(table)} FROM {prepare.quote(role)}")

    for table, indexes in (
        ("tally_export_items", ("ix_tally_export_items_remote_id", "ix_tally_export_items_target_fingerprint", "ix_tally_export_items_batch_outcome")),
        ("tally_export_batches", ("ix_tally_export_batches_profile", "ix_tally_export_batches_run", "ix_tally_export_batches_workspace_client_created")),
        ("tally_purchase_ledger_mappings", ("ix_tally_purchase_mappings_profile", "ix_tally_purchase_mappings_rate_lookup")),
        ("tally_party_ledger_mappings", ("ix_tally_party_mappings_confirmed_by", "ix_tally_party_mappings_exact_lookup")),
        ("tally_export_profiles", ("ix_tally_export_profiles_updated_by", "ix_tally_export_profiles_created_by", "ix_tally_export_profiles_lookup")),
    ):
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
    op.drop_constraint(
        op.f("uq_reconciliation_runs_workspace_id_client_id_client_gstin_id_id"),
        "reconciliation_runs", type_="unique",
    )
