"""Add durable Tally candidates, idempotency, and history snapshots."""

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260923_14"
down_revision = "20260923_13"
branch_labels = depends_on = None


def _application_role():
    explicit = os.getenv("DATABASE_ROLE", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("DATABASE_URL", "").strip()
    return make_url(raw).username if raw else None


def upgrade():
    op.add_column(
        "reconciliation_runs",
        sa.Column("tally_candidate_snapshot_version", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_reconciliation_runs_tally_candidate_snapshot_version"),
        "reconciliation_runs",
        "tally_candidate_snapshot_version IS NULL OR "
        "(tally_candidate_snapshot_version >= 1 AND tally_candidate_snapshot_version <= result_version)",
    )

    op.create_table(
        "tally_export_candidate_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("client_gstin_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_run_id", sa.Uuid(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.String(length=255), nullable=False),
        sa.Column("remote_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("transaction_type", sa.String(length=40), nullable=False),
        sa.Column("tax_structure", sa.String(length=30), nullable=False),
        sa.Column("supplier_gstin", sa.String(length=15), nullable=False),
        sa.Column("supplier_gstin_valid", sa.Boolean(), nullable=False),
        sa.Column("supplier_name", sa.String(length=1024), nullable=False),
        sa.Column("document_number", sa.String(length=1024), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("original_invoice_number", sa.String(length=1024), nullable=False),
        sa.Column("taxable_value", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("igst", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("cgst", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("sgst", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("cess", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("cess_present", sa.Boolean(), nullable=False),
        sa.Column("invoice_total", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("gst_rates_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("rate_allocations_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("gst_rate_missing", sa.Boolean(), nullable=False),
        sa.Column("place_of_supply", sa.String(length=255), nullable=False),
        sa.Column("place_of_supply_values_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("place_of_supply_missing", sa.Boolean(), nullable=False),
        sa.Column("place_of_supply_conflicting", sa.Boolean(), nullable=False),
        sa.Column("reverse_charge_status", sa.String(length=20), nullable=False),
        sa.Column("reverse_charge_values_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("is_amendment", sa.Boolean(), nullable=False),
        sa.Column("amendment_original_number", sa.String(length=1024), nullable=False),
        sa.Column("amendment_original_date", sa.Date(), nullable=True),
        sa.Column("is_cancelled", sa.Boolean(), nullable=False),
        sa.Column("cancellation_evidence_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("financial_parse_issues_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("source_identity_json", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.CheckConstraint("result_version >= 1", name=op.f("ck_tally_export_candidate_snapshots_result_version")),
        sa.CheckConstraint("transaction_type = 'purchase_invoice'", name=op.f("ck_tally_export_candidate_snapshots_transaction_type")),
        sa.CheckConstraint(
            "tax_structure IN ('no_tax', 'igst_only', 'cgst_sgst', 'mixed_or_inconsistent')",
            name=op.f("ck_tally_export_candidate_snapshots_tax_structure"),
        ),
        sa.CheckConstraint("length(btrim(candidate_id)) > 0", name=op.f("ck_tally_export_candidate_snapshots_candidate_id_not_blank")),
        sa.CheckConstraint("transaction_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_tally_export_candidate_snapshots_transaction_fingerprint")),
        sa.CheckConstraint("length(btrim(supplier_name)) > 0", name=op.f("ck_tally_export_candidate_snapshots_supplier_name_not_blank")),
        sa.CheckConstraint("length(btrim(document_number)) > 0", name=op.f("ck_tally_export_candidate_snapshots_document_number_not_blank")),
        sa.CheckConstraint("jsonb_typeof(gst_rates_json) = 'array' AND octet_length(gst_rates_json::text) <= 4096", name=op.f("ck_tally_export_candidate_snapshots_gst_rates_bounded_array")),
        sa.CheckConstraint("jsonb_typeof(rate_allocations_json) = 'array' AND octet_length(rate_allocations_json::text) <= 16384", name=op.f("ck_tally_export_candidate_snapshots_rate_alloc_bounded")),
        sa.CheckConstraint("jsonb_typeof(place_of_supply_values_json) = 'array' AND octet_length(place_of_supply_values_json::text) <= 4096", name=op.f("ck_tally_export_candidate_snapshots_place_values_bounded_array")),
        sa.CheckConstraint("jsonb_typeof(reverse_charge_values_json) = 'array' AND octet_length(reverse_charge_values_json::text) <= 4096", name=op.f("ck_tally_export_candidate_snapshots_rcm_values_bounded")),
        sa.CheckConstraint("jsonb_typeof(cancellation_evidence_json) = 'array' AND octet_length(cancellation_evidence_json::text) <= 8192", name=op.f("ck_tally_export_candidate_snapshots_cancellation_bounded_array")),
        sa.CheckConstraint("jsonb_typeof(financial_parse_issues_json) = 'array' AND octet_length(financial_parse_issues_json::text) <= 16384", name=op.f("ck_tally_export_candidate_snapshots_parse_issues_bounded")),
        sa.CheckConstraint("jsonb_typeof(source_identity_json) = 'array' AND octet_length(source_identity_json::text) <= 65536", name=op.f("ck_tally_export_candidate_snapshots_source_rows_bounded")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"],
            name=op.f("fk_tally_export_candidate_snapshots_workspace_id_client_id_clients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            name=op.f("fk_tally_export_candidate_snapshots_workspace_id_client_id_client_gstin_id_client_gstins"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id", "reconciliation_run_id"],
            ["reconciliation_runs.workspace_id", "reconciliation_runs.client_id", "reconciliation_runs.client_gstin_id", "reconciliation_runs.id"],
            name=op.f("fk_tally_export_candidate_snapshots_workspace_id_client_id_client_gstin_id_reconciliation_run_id_reconciliation_runs"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tally_export_candidate_snapshots")),
        sa.UniqueConstraint("reconciliation_run_id", "result_version", "candidate_id", name=op.f("uq_tally_export_candidate_snapshots_reconciliation_run_id_result_version_candidate_id")),
    )
    op.create_index(
        "ix_tally_candidate_snapshots_run_version", "tally_export_candidate_snapshots",
        ["workspace_id", "client_id", "client_gstin_id", "reconciliation_run_id", "result_version"],
    )
    op.create_index(
        "ix_tally_candidate_snapshots_remote_id", "tally_export_candidate_snapshots", ["remote_id"]
    )

    op.add_column("tally_export_batches", sa.Column("operation_key", sa.Uuid(), nullable=True))
    op.add_column("tally_export_batches", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("tally_export_batches", sa.Column("failure_code", sa.String(length=100), nullable=True))
    op.add_column("tally_export_batches", sa.Column("failure_message", sa.String(length=500), nullable=True))
    op.create_check_constraint(
        op.f("ck_tally_export_batches_request_fingerprint"), "tally_export_batches",
        "request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_tally_export_batches_idempotency_metadata"), "tally_export_batches",
        "(operation_key IS NULL AND request_fingerprint IS NULL) OR "
        "(operation_key IS NOT NULL AND request_fingerprint IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_tally_export_batches_failure_message_bounded"), "tally_export_batches",
        "failure_message IS NULL OR length(failure_message) <= 500",
    )
    op.create_check_constraint(
        op.f("ck_tally_export_batches_failure_code_format"), "tally_export_batches",
        "failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,99}$'",
    )
    op.create_check_constraint(
        op.f("ck_tally_export_batches_failure_metadata"), "tally_export_batches",
        "(failure_code IS NULL AND failure_message IS NULL) OR "
        "(failure_code IS NOT NULL AND failure_message IS NOT NULL)",
    )
    op.create_unique_constraint(
        op.f("uq_tally_export_batches_workspace_id_client_id_client_gstin_id_created_by_operation_key"),
        "tally_export_batches", ["workspace_id", "client_id", "client_gstin_id", "created_by", "operation_key"],
    )
    op.create_index(
        "ix_tally_export_batches_operation", "tally_export_batches",
        ["workspace_id", "created_by", "operation_key"],
    )

    op.add_column(
        "tally_export_items",
        sa.Column("display_snapshot_json", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_tally_export_items_display_snapshot_bounded_object"), "tally_export_items",
        "display_snapshot_json IS NULL OR "
        "(jsonb_typeof(display_snapshot_json) = 'object' AND octet_length(display_snapshot_json::text) <= 8192)",
    )

    role = _application_role()
    if role:
        prepare = op.get_bind().dialect.identifier_preparer
        table = prepare.quote("tally_export_candidate_snapshots")
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO {prepare.quote(role)}")


def downgrade():
    role = _application_role()
    if role:
        prepare = op.get_bind().dialect.identifier_preparer
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {prepare.quote('tally_export_candidate_snapshots')} "
            f"FROM {prepare.quote(role)}"
        )

    op.drop_constraint(op.f("ck_tally_export_items_display_snapshot_bounded_object"), "tally_export_items", type_="check")
    op.drop_column("tally_export_items", "display_snapshot_json")

    op.drop_index("ix_tally_export_batches_operation", table_name="tally_export_batches")
    op.drop_constraint(
        op.f("uq_tally_export_batches_workspace_id_client_id_client_gstin_id_created_by_operation_key"),
        "tally_export_batches", type_="unique",
    )
    op.drop_constraint(op.f("ck_tally_export_batches_failure_message_bounded"), "tally_export_batches", type_="check")
    op.drop_constraint(op.f("ck_tally_export_batches_failure_metadata"), "tally_export_batches", type_="check")
    op.drop_constraint(op.f("ck_tally_export_batches_failure_code_format"), "tally_export_batches", type_="check")
    op.drop_constraint(op.f("ck_tally_export_batches_idempotency_metadata"), "tally_export_batches", type_="check")
    op.drop_constraint(op.f("ck_tally_export_batches_request_fingerprint"), "tally_export_batches", type_="check")
    op.drop_column("tally_export_batches", "failure_message")
    op.drop_column("tally_export_batches", "failure_code")
    op.drop_column("tally_export_batches", "request_fingerprint")
    op.drop_column("tally_export_batches", "operation_key")

    op.drop_index("ix_tally_candidate_snapshots_remote_id", table_name="tally_export_candidate_snapshots")
    op.drop_index("ix_tally_candidate_snapshots_run_version", table_name="tally_export_candidate_snapshots")
    op.drop_table("tally_export_candidate_snapshots")
    op.drop_constraint(
        op.f("ck_reconciliation_runs_tally_candidate_snapshot_version"),
        "reconciliation_runs", type_="check",
    )
    op.drop_column("reconciliation_runs", "tally_candidate_snapshot_version")
