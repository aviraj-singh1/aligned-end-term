"""Add tenant-owned GSTR merge run metadata and lifecycle state."""

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260914_12"
down_revision = "20260910_11"
branch_labels = depends_on = None


def _application_role():
    explicit = os.getenv("DATABASE_ROLE", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("DATABASE_URL", "").strip()
    return make_url(raw).username if raw else None


def upgrade():
    op.create_table(
        "gstr_merge_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("client_gstin_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("financial_year", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="UPLOADING", nullable=False),
        sa.Column("input_manifest_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("result_summary_json", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("output_object_ref", sa.String(length=1024), nullable=True),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("engine_version", sa.String(length=100), server_default="1.0.0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("safe_error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('QUARTERLY', 'MONTHLY')", name=op.f("ck_gstr_merge_runs_mode")),
        sa.CheckConstraint(
            "status IN ('UPLOADING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'EXPIRED', 'DELETED')",
            name=op.f("ck_gstr_merge_runs_status"),
        ),
        sa.CheckConstraint(
            "financial_year ~ '^20[0-9]{2}-[0-9]{2}$' "
            "AND ((substring(financial_year, 1, 4)::integer + 1) % 100) "
            "= substring(financial_year, 6, 2)::integer",
            name=op.f("ck_gstr_merge_runs_financial_year"),
        ),
        sa.CheckConstraint("version >= 1 AND attempt_count >= 0", name=op.f("ck_gstr_merge_runs_versions")),
        sa.CheckConstraint(
            "input_manifest_json IS NULL OR "
            "(jsonb_typeof(input_manifest_json) = 'object' AND octet_length(input_manifest_json::text) <= 65536)",
            name=op.f("ck_gstr_merge_runs_input_manifest_bounded_object"),
        ),
        sa.CheckConstraint(
            "result_summary_json IS NULL OR "
            "(jsonb_typeof(result_summary_json) = 'object' AND octet_length(result_summary_json::text) <= 65536)",
            name=op.f("ck_gstr_merge_runs_result_summary_bounded_object"),
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_gstr_merge_runs_output_sha256"),
        ),
        sa.CheckConstraint(
            "(output_object_ref IS NULL AND output_size_bytes IS NULL AND output_sha256 IS NULL) OR "
            "(output_object_ref IS NOT NULL AND length(btrim(output_object_ref)) > 0 "
            "AND output_size_bytes IS NOT NULL AND output_size_bytes >= 0 AND output_sha256 IS NOT NULL)",
            name=op.f("ck_gstr_merge_runs_output_metadata"),
        ),
        sa.CheckConstraint("length(btrim(engine_version)) > 0", name=op.f("ck_gstr_merge_runs_engine_version")),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name=op.f("ck_gstr_merge_runs_lease_pair"),
        ),
        sa.CheckConstraint("status <> 'PROCESSING' OR started_at IS NOT NULL", name=op.f("ck_gstr_merge_runs_processing_state")),
        sa.CheckConstraint("status <> 'COMPLETED' OR completed_at IS NOT NULL", name=op.f("ck_gstr_merge_runs_completed_state")),
        sa.CheckConstraint(
            "status <> 'FAILED' OR "
            "(failed_at IS NOT NULL AND error_code IS NOT NULL AND length(btrim(error_code)) > 0)",
            name=op.f("ck_gstr_merge_runs_failed_state"),
        ),
        sa.CheckConstraint("status <> 'EXPIRED' OR expires_at IS NOT NULL", name=op.f("ck_gstr_merge_runs_expired_state")),
        sa.CheckConstraint("(status = 'DELETED') = (deleted_at IS NOT NULL)", name=op.f("ck_gstr_merge_runs_deleted_state")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name=op.f("fk_gstr_merge_runs_created_by_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"],
            name=op.f("fk_gstr_merge_runs_workspace_id_client_id_clients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            name=op.f("fk_gstr_merge_runs_workspace_id_client_id_client_gstin_id_client_gstins"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gstr_merge_runs")),
        sa.UniqueConstraint("workspace_id", "client_id", "id", name=op.f("uq_gstr_merge_runs_workspace_id_client_id_id")),
    )
    op.create_index(
        "ix_gstr_merge_runs_workspace_client_created", "gstr_merge_runs",
        ["workspace_id", "client_id", "created_at", "id"], unique=False,
    )
    op.create_index(
        "ix_gstr_merge_runs_queue", "gstr_merge_runs",
        ["status", "next_attempt_at", "created_at", "id"], unique=False,
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.create_index("ix_gstr_merge_runs_created_by", "gstr_merge_runs", ["created_by"], unique=False)

    role = _application_role()
    if role:
        prepare = op.get_bind().dialect.identifier_preparer
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE ON TABLE {prepare.quote('gstr_merge_runs')} "
            f"TO {prepare.quote(role)}"
        )


def downgrade():
    role = _application_role()
    if role:
        prepare = op.get_bind().dialect.identifier_preparer
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {prepare.quote('gstr_merge_runs')} "
            f"FROM {prepare.quote(role)}"
        )
    op.drop_index("ix_gstr_merge_runs_created_by", table_name="gstr_merge_runs")
    op.drop_index("ix_gstr_merge_runs_queue", table_name="gstr_merge_runs", postgresql_where=sa.text("status = 'QUEUED'"))
    op.drop_index("ix_gstr_merge_runs_workspace_client_created", table_name="gstr_merge_runs")
    op.drop_table("gstr_merge_runs")
