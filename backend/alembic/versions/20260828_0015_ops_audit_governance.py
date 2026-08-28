"""Phase 8 operations governance & audit: quota incident history table and the
indexes required by the paginated/filtered admin audit and overview queries."""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0015"
down_revision = "20260828_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("quota_incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_quota_incidents_household_id", "quota_incidents", ["household_id"])
    op.create_index("ix_quota_incidents_created_at", "quota_incidents", ["created_at"])

    op.create_index("ix_agent_runs_status_created", "agent_runs", ["status", "created_at"])
    op.create_index("ix_agent_runs_deployment_id", "agent_runs", ["deployment_id"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])
    op.create_index("ix_tool_executions_run_id", "tool_executions", ["run_id"])
    op.create_index("ix_tool_executions_status_started", "tool_executions", ["status", "started_at"])
    op.create_index("ix_tool_executions_tool_id", "tool_executions", ["tool_id"])
    op.create_index("ix_model_usage_household_month", "model_usage", ["household_id", "month"])
    op.create_index("ix_model_usage_model_id", "model_usage", ["model_id"])
    op.create_index("ix_model_usage_status_created", "model_usage", ["status", "created_at"])
    op.create_index("ix_model_usage_created_at", "model_usage", ["created_at"])
    op.create_index("ix_celery_tasks_status_created", "celery_tasks", ["status", "created_at"])
    op.create_index("ix_celery_tasks_error_code", "celery_tasks", ["error_code"])
    op.create_index("ix_admin_action_audits_action_created", "admin_action_audits", ["action", "created_at"])
    op.create_index("ix_admin_action_audits_resource", "admin_action_audits", ["resource_type", "resource_id"])
    op.create_index("ix_admin_action_audits_created_at", "admin_action_audits", ["created_at"])
    op.create_index("ix_review_submissions_status_created", "review_submissions", ["status", "created_at"])
    op.create_index("ix_review_submissions_entry_id", "review_submissions", ["entry_id"])


def downgrade() -> None:
    op.drop_index("ix_review_submissions_entry_id", table_name="review_submissions")
    op.drop_index("ix_review_submissions_status_created", table_name="review_submissions")
    op.drop_index("ix_admin_action_audits_created_at", table_name="admin_action_audits")
    op.drop_index("ix_admin_action_audits_resource", table_name="admin_action_audits")
    op.drop_index("ix_admin_action_audits_action_created", table_name="admin_action_audits")
    op.drop_index("ix_celery_tasks_error_code", table_name="celery_tasks")
    op.drop_index("ix_celery_tasks_status_created", table_name="celery_tasks")
    op.drop_index("ix_model_usage_created_at", table_name="model_usage")
    op.drop_index("ix_model_usage_status_created", table_name="model_usage")
    op.drop_index("ix_model_usage_model_id", table_name="model_usage")
    op.drop_index("ix_model_usage_household_month", table_name="model_usage")
    op.drop_index("ix_tool_executions_tool_id", table_name="tool_executions")
    op.drop_index("ix_tool_executions_status_started", table_name="tool_executions")
    op.drop_index("ix_tool_executions_run_id", table_name="tool_executions")
    op.drop_index("ix_agent_runs_created_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_deployment_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status_created", table_name="agent_runs")
    op.drop_index("ix_quota_incidents_created_at", table_name="quota_incidents")
    op.drop_index("ix_quota_incidents_household_id", table_name="quota_incidents")
    op.drop_table("quota_incidents")
