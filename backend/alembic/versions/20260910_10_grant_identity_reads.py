"""Allow the application role to read external authentication identity links."""
import os

from alembic import op
from sqlalchemy.engine import make_url

revision = "20260910_10"
down_revision = "20260905_09"
branch_labels = depends_on = None


def _application_role():
    explicit = os.getenv("DATABASE_ROLE", "").strip()
    if explicit:
        return explicit
    raw = os.getenv("DATABASE_URL", "").strip()
    return make_url(raw).username if raw else None


def upgrade():
    role = _application_role()
    if role:
        quoted = op.get_bind().dialect.identifier_preparer.quote(role)
        op.execute(f"GRANT SELECT ON TABLE auth_identities TO {quoted}")


def downgrade():
    role = _application_role()
    if role:
        quoted = op.get_bind().dialect.identifier_preparer.quote(role)
        op.execute(f"REVOKE SELECT ON TABLE auth_identities FROM {quoted}")
