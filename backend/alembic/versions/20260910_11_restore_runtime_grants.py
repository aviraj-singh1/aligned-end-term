"""Restore least-privilege runtime access for all application tables."""
import os

from alembic import op
from sqlalchemy.engine import make_url

revision = "20260910_11"
down_revision = "20260910_10"
branch_labels = depends_on = None


READ_WRITE_TABLES = (
    "users",
    "password_credentials",
    "auth_sessions",
    "auth_action_tokens",
    "workspaces",
    "workspace_memberships",
    "workspace_invites",
    "clients",
    "client_gstins",
    "reconciliation_runs",
    "run_operations",
    "ownership_transfers",
)
APPEND_ONLY_TABLES = ("audit_events", "auth_security_events")


def _application_role():
    explicit = os.getenv("DATABASE_ROLE", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("DATABASE_URL", "").strip()
    return make_url(raw).username if raw else None


def upgrade():
    role = _application_role()
    if not role:
        return
    prepare = op.get_bind().dialect.identifier_preparer
    quoted_role = prepare.quote(role)
    for table in READ_WRITE_TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE ON TABLE {prepare.quote(table)} TO {quoted_role}"
        )
    for table in APPEND_ONLY_TABLES:
        op.execute(f"GRANT SELECT, INSERT ON TABLE {prepare.quote(table)} TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE auth_identities TO {quoted_role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE client_assignments TO {quoted_role}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE reconciliation_session_state TO {quoted_role}"
    )


def downgrade():
    role = _application_role()
    if not role:
        return
    prepare = op.get_bind().dialect.identifier_preparer
    quoted_role = prepare.quote(role)
    tables = (*READ_WRITE_TABLES, *APPEND_ONLY_TABLES, "auth_identities", "client_assignments", "reconciliation_session_state")
    for table in tables:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {prepare.quote(table)} FROM {quoted_role}")
