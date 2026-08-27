"""Secured Tool, Skill and Agent platform runtime."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0005"
down_revision = "20260827_0004"
branch_labels = None
depends_on = None


def _add(table, *columns):
    with op.batch_alter_table(table) as batch:
        for column in columns:
            batch.add_column(column)


def upgrade() -> None:
    _add("chat_sessions", sa.Column("deployment_id", sa.String(36), nullable=True))
    op.create_index("ix_chat_sessions_deployment_id", "chat_sessions", ["deployment_id"])
    _add("tool_definitions",
         sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
         sa.Column("description", sa.Text(), nullable=False, server_default=""),
         sa.Column("version", sa.String(32), nullable=False, server_default="1"),
         sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
         sa.Column("catalog_json", sa.Text(), nullable=False, server_default="{}"))
    _add("tool_permissions",
         sa.Column("tool_version", sa.String(32), nullable=False, server_default="1"),
         sa.Column("granted_by_account_id", sa.String(36), nullable=True))
    with op.batch_alter_table("tool_permissions") as batch:
        batch.create_unique_constraint("uq_tool_permission_household_tool", ["household_id", "tool_id"])
        batch.create_foreign_key("fk_tool_permissions_granted_by", "accounts", ["granted_by_account_id"], ["id"])
    _add("agent_deployments",
         sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
         sa.Column("visibility", sa.String(16), nullable=False, server_default="private"))
    _add("agent_installations",
         sa.Column("agent_version_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("status", sa.String(16), nullable=False, server_default="installed"))
    _add("installation_permissions",
         sa.Column("tool_version", sa.String(32), nullable=False, server_default="1"),
         sa.Column("granted_by_account_id", sa.String(36), nullable=True),
         sa.Column("created_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("installation_permissions") as batch:
        batch.create_foreign_key("fk_installation_permissions_granted_by", "accounts", ["granted_by_account_id"], ["id"])
    _add("agent_runs",
         sa.Column("principal_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("member_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("session_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("agent_version_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("deployment_revision", sa.Integer(), nullable=False, server_default="1"),
         sa.Column("model_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("model_revision", sa.Integer(), nullable=False, server_default="1"),
         sa.Column("skill_versions_json", sa.Text(), nullable=False, server_default="[]"),
         sa.Column("status", sa.String(32), nullable=False, server_default="running"),
         sa.Column("error_code", sa.String(64), nullable=True),
         sa.Column("finished_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("agent_runs") as batch:
        batch.create_foreign_key("fk_agent_runs_principal", "accounts", ["principal_id"], ["id"])
        batch.create_foreign_key("fk_agent_runs_member", "household_members", ["member_id"], ["id"])
    _add("tool_executions",
         sa.Column("principal_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("agent_version_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("skill_versions_json", sa.Text(), nullable=False, server_default="[]"),
         sa.Column("tool_version", sa.String(32), nullable=False, server_default="1"),
         sa.Column("model_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("deployment_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("deployment_revision", sa.Integer(), nullable=False, server_default="1"),
         sa.Column("status", sa.String(32), nullable=False, server_default="running"),
         sa.Column("error_code", sa.String(64), nullable=True),
         sa.Column("idempotency_key", sa.String(100), nullable=True),
         sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
         sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
         sa.Column("started_at", sa.DateTime(), nullable=True),
         sa.Column("finished_at", sa.DateTime(), nullable=True),
         sa.Column("duration_ms", sa.Integer(), nullable=True))
    with op.batch_alter_table("tool_executions") as batch:
        batch.create_unique_constraint("uq_tool_executions_idempotency", ["idempotency_key"])
        batch.create_foreign_key("fk_tool_executions_principal", "accounts", ["principal_id"], ["id"])
    _add("approval_requests",
         sa.Column("principal_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("tool_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("tool_version", sa.String(32), nullable=False, server_default="1"),
         sa.Column("deployment_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("deployment_revision", sa.Integer(), nullable=False, server_default="1"),
         sa.Column("agent_version_id", sa.String(36), nullable=False, server_default=""),
         sa.Column("arguments_digest", sa.String(64), nullable=False, server_default=""),
         sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
         sa.Column("idempotency_key", sa.String(100), nullable=True),
         sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
         sa.Column("approved_by_account_id", sa.String(36), nullable=True),
         sa.Column("expires_at", sa.DateTime(), nullable=True),
         sa.Column("consumed_at", sa.DateTime(), nullable=True),
         sa.Column("result_json", sa.Text(), nullable=True),
         sa.Column("created_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("approval_requests") as batch:
        batch.create_unique_constraint("uq_approval_requests_idempotency", ["idempotency_key"])
        batch.create_foreign_key("fk_approval_requests_principal", "accounts", ["principal_id"], ["id"])
        batch.create_foreign_key("fk_approval_requests_tool", "tool_definitions", ["tool_id"], ["id"])
        batch.create_foreign_key("fk_approval_requests_approved_by", "accounts", ["approved_by_account_id"], ["id"])
    op.create_table("agent_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("household_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False), sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("thread_id", sa.String(100), nullable=False), sa.Column("checkpoint_ns", sa.String(250), nullable=False),
        sa.Column("checkpoint_id", sa.String(100), nullable=False), sa.Column("parent_checkpoint_id", sa.String(100)),
        sa.Column("checkpoint_blob", sa.LargeBinary(), nullable=False), sa.Column("metadata_blob", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime()), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", name="uq_agent_checkpoint_key"))
    op.create_index("ix_agent_checkpoints_household_id", "agent_checkpoints", ["household_id"])
    op.create_table("agent_checkpoint_writes",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("household_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False), sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("thread_id", sa.String(100), nullable=False), sa.Column("checkpoint_ns", sa.String(250), nullable=False),
        sa.Column("checkpoint_id", sa.String(100), nullable=False), sa.Column("task_id", sa.String(100), nullable=False),
        sa.Column("task_path", sa.String(250), nullable=False, server_default=""), sa.Column("write_index", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False), sa.Column("value_blob", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index", name="uq_agent_checkpoint_write"))
    op.create_index("ix_agent_checkpoint_writes_household_id", "agent_checkpoint_writes", ["household_id"])


def downgrade() -> None:
    op.drop_table("agent_checkpoint_writes")
    op.drop_table("agent_checkpoints")
    op.drop_index("ix_chat_sessions_deployment_id", table_name="chat_sessions")
    for table, columns in (
        ("approval_requests", ["created_at", "result_json", "consumed_at", "expires_at", "approved_by_account_id", "status", "idempotency_key", "arguments_json", "arguments_digest", "agent_version_id", "deployment_revision", "deployment_id", "tool_version", "tool_id", "principal_id"]),
        ("tool_executions", ["duration_ms", "finished_at", "started_at", "result_json", "arguments_json", "idempotency_key", "error_code", "status", "deployment_revision", "deployment_id", "model_id", "tool_version", "skill_versions_json", "agent_version_id", "principal_id"]),
        ("agent_runs", ["finished_at", "error_code", "status", "skill_versions_json", "model_revision", "model_id", "deployment_revision", "agent_version_id", "session_id", "member_id", "principal_id"]),
        ("installation_permissions", ["created_at", "granted_by_account_id", "tool_version"]),
        ("agent_installations", ["status", "agent_version_id"]),
        ("agent_deployments", ["visibility", "revision"]),
        ("tool_permissions", ["granted_by_account_id", "tool_version"]),
        ("tool_definitions", ["catalog_json", "enabled", "version", "description", "display_name"]),
        ("chat_sessions", ["deployment_id"]),
    ):
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_column(column)
