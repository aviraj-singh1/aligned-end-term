"""Add verified phone and workspace address fields for staged onboarding."""
from alembic import op
import sqlalchemy as sa

revision = "20260902_07"
down_revision = "20260901_06"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("phone_number", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("uq_users_verified_phone", "users", ["phone_number"], unique=True,
                    postgresql_where=sa.text("phone_verified_at IS NOT NULL"))
    op.add_column("auth_identities", sa.Column("provider", sa.String(40), nullable=False,
                                               server_default="EXTERNAL"))
    for name, length in (("address_line1", 255), ("address_line2", 255), ("city", 120),
                         ("state", 120), ("postal_code", 20), ("country_code", 2)):
        op.add_column("workspaces", sa.Column(name, sa.String(length), nullable=True))


def downgrade():
    for name in ("country_code", "postal_code", "state", "city", "address_line2", "address_line1"):
        op.drop_column("workspaces", name)
    op.drop_column("auth_identities", "provider")
    op.drop_index("uq_users_verified_phone", table_name="users")
    op.drop_column("users", "phone_verified_at")
    op.drop_column("users", "phone_number")
