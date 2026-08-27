"""Fix Round 3 run outcomes and active installation authority."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0008"
down_revision = "20260827_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.execute(sa.text("""
        SELECT household_id, deployment_id FROM agent_installations
        WHERE status = 'installed'
        GROUP BY household_id, deployment_id HAVING COUNT(*) > 1
    """)).first()
    if duplicates:
        raise RuntimeError("INSTALLATION_AUTHORITY_REPAIR_REQUIRED")
    with op.batch_alter_table("agent_installations") as batch:
        batch.add_column(sa.Column("authority_slot", sa.String(16), nullable=True))
    connection.execute(sa.text("UPDATE agent_installations SET authority_slot='active' WHERE status='installed'"))
    with op.batch_alter_table("agent_installations") as batch:
        batch.create_unique_constraint("uq_agent_installation_active_slot",
                                       ["household_id", "deployment_id", "authority_slot"])
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("interrupted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("resumed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("resumed_at")
        batch.drop_column("interrupted_at")
        batch.drop_column("started_at")
    with op.batch_alter_table("agent_installations") as batch:
        batch.drop_constraint("uq_agent_installation_active_slot", type_="unique")
        batch.drop_column("authority_slot")
