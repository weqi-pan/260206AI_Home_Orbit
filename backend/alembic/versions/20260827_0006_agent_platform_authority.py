"""Fix Round 1 authoritative installation runtime and snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0006"
down_revision = "20260827_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("household_id", sa.String(36), nullable=True))
        batch.create_index("ix_chat_sessions_household_id", ["household_id"])
        batch.create_foreign_key("fk_chat_sessions_household", "households", ["household_id"], ["id"], ondelete="CASCADE")
        batch.create_foreign_key("fk_chat_sessions_deployment_household", "agent_deployments",
                                 ["deployment_id", "household_id"], ["id", "household_id"])
    with op.batch_alter_table("agent_installations") as batch:
        batch.create_foreign_key("fk_installations_agent_version_household", "agent_versions",
                                 ["agent_version_id", "household_id"], ["id", "household_id"])
    with op.batch_alter_table("installation_permissions") as batch:
        batch.create_unique_constraint("uq_installation_permission_tool", ["installation_id", "tool_id"])
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("compiled_snapshot_json", sa.Text(), nullable=False))
        batch.add_column(sa.Column("model_snapshot_json", sa.Text(), nullable=False))
        batch.add_column(sa.Column("permission_snapshot_json", sa.Text(), nullable=False))
        batch.add_column(sa.Column("consent_snapshot_json", sa.Text(), nullable=False))
        batch.add_column(sa.Column("budget_snapshot_json", sa.Text(), nullable=False))
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("checkpoint_ns", sa.String(250), nullable=True))
        batch.add_column(sa.Column("checkpoint_id", sa.String(100), nullable=True))
        batch.add_column(sa.Column("consent_digest", sa.String(64), nullable=True))
    with op.batch_alter_table("tool_executions") as batch:
        batch.add_column(sa.Column("redaction_schema_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("tool_executions") as batch:
        batch.drop_column("redaction_schema_version")
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_column("consent_digest"); batch.drop_column("checkpoint_id"); batch.drop_column("checkpoint_ns")
    with op.batch_alter_table("agent_runs") as batch:
        for column in ("budget_snapshot_json", "consent_snapshot_json", "permission_snapshot_json",
                       "model_snapshot_json", "compiled_snapshot_json"):
            batch.drop_column(column)
    with op.batch_alter_table("installation_permissions") as batch:
        batch.drop_constraint("uq_installation_permission_tool", type_="unique")
    with op.batch_alter_table("agent_installations") as batch:
        batch.drop_constraint("fk_installations_agent_version_household", type_="foreignkey")
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_constraint("fk_chat_sessions_deployment_household", type_="foreignkey")
        batch.drop_constraint("fk_chat_sessions_household", type_="foreignkey")
        batch.drop_index("ix_chat_sessions_household_id")
        batch.drop_column("household_id")
