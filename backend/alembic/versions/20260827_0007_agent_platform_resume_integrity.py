"""Fix Round 2 resume integrity and deterministic tenant backfill."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0007"
down_revision = "20260827_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    invalid = connection.execute(sa.text("""
        SELECT cs.id
        FROM chat_sessions cs
        LEFT JOIN household_members hm ON hm.account_id = cs.account_id
        GROUP BY cs.id
        HAVING COUNT(hm.household_id) <> 1
    """)).first()
    if invalid:
        raise RuntimeError("CHAT_SESSION_HOUSEHOLD_BACKFILL_REPAIR_REQUIRED")
    connection.execute(sa.text("""
        UPDATE chat_sessions
        SET household_id = (
            SELECT hm.household_id FROM household_members hm
            WHERE hm.account_id = chat_sessions.account_id
        )
        WHERE household_id IS NULL
    """))
    with op.batch_alter_table("chat_sessions") as batch:
        batch.alter_column("household_id", existing_type=sa.String(36), nullable=False)
    with op.batch_alter_table("agent_installations") as batch:
        batch.create_unique_constraint("uq_agent_installation_authority",
                                       ["household_id", "deployment_id", "agent_version_id"])
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("error_detail", sa.String(100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_column("error_detail"); batch.drop_column("error_code"); batch.drop_column("claimed_at")
    with op.batch_alter_table("agent_installations") as batch:
        batch.drop_constraint("uq_agent_installation_authority", type_="unique")
    with op.batch_alter_table("chat_sessions") as batch:
        batch.alter_column("household_id", existing_type=sa.String(36), nullable=True)
