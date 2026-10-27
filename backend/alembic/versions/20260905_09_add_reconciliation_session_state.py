"""Add shared reconciliation session state for Supabase/PostgreSQL deployments."""
import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260905_09"
down_revision = "20260902_08"
branch_labels = depends_on = None


def _application_role():
    explicit = os.getenv("DATABASE_ROLE", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("DATABASE_URL", "").strip()
    return make_url(raw).username if raw else None


def upgrade():
    op.create_table(
        "reconciliation_session_state",
        sa.Column("session_id", sa.String(length=36), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_reconciliation_session_state_expires_at", "reconciliation_session_state", ["expires_at"])
    role = _application_role()
    if role:
        quoted = op.get_bind().dialect.identifier_preparer.quote(role)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE reconciliation_session_state TO {quoted}")


def downgrade():
    op.drop_index("ix_reconciliation_session_state_expires_at", table_name="reconciliation_session_state")
    op.drop_table("reconciliation_session_state")
