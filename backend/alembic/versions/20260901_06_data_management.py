"""Add a workspace reset boundary and permit GSTIN reuse after a reset."""
from alembic import op
import sqlalchemy as sa

revision = "20260901_06"
down_revision = "20260901_05"
branch_labels = depends_on = None


def upgrade():
    op.add_column("workspaces", sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(op.f("uq_client_gstins_workspace_id_gstin"), "client_gstins", type_="unique")
    op.create_index(
        "uq_client_gstins_active_workspace_gstin",
        "client_gstins",
        ["workspace_id", "gstin"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade():
    # Refuse a lossy downgrade if a reset allowed the same GSTIN to be reused.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM client_gstins GROUP BY workspace_id,gstin HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'Cannot restore the old GSTIN constraint while duplicate historical GSTINs exist';
      END IF;
    END $$""")
    op.drop_index("uq_client_gstins_active_workspace_gstin", table_name="client_gstins")
    op.create_unique_constraint(op.f("uq_client_gstins_workspace_id_gstin"), "client_gstins", ["workspace_id", "gstin"])
    op.drop_column("workspaces", "reset_at")
