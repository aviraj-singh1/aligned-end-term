"""Allow the application role to maintain external authentication identity links."""
import os

from alembic import op
from sqlalchemy.engine import make_url

revision = "20260902_08"
down_revision = "20260902_07"
branch_labels = depends_on = None


def _application_role():
    explicit = os.getenv("DATABASE_ROLE", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("DATABASE_URL", "").strip()
    return make_url(raw).username if raw else None


def _quoted_role(role):
    return op.get_bind().dialect.identifier_preparer.quote(role)


def upgrade():
    role = _application_role()
    if role:
        op.execute(f"GRANT INSERT, UPDATE, DELETE ON TABLE auth_identities TO {_quoted_role(role)}")


def downgrade():
    role = _application_role()
    if role:
        op.execute(f"REVOKE INSERT, UPDATE, DELETE ON TABLE auth_identities FROM {_quoted_role(role)}")
