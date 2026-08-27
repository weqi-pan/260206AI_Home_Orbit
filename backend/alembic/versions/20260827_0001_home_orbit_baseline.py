"""Home Orbit clean-schema baseline."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0001"
down_revision = None
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.String(36), primary_key=True)


def _household_id():
    return sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)


def _created_at():
    return sa.Column("created_at", sa.DateTime(), nullable=True)


def upgrade() -> None:
    op.create_table("users", sa.Column("user_id", sa.String(32), primary_key=True), sa.Column("username", sa.String(100), nullable=False), sa.Column("city", sa.String(100), nullable=False), sa.Column("phone", sa.String(32)), _created_at())
    op.create_table("accounts", _id(), sa.Column("username", sa.String(64), nullable=False), sa.Column("password_hash", sa.String(128), nullable=False), sa.Column("role", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("must_change_password", sa.Boolean(), nullable=False), sa.Column("user_id", sa.String(32), sa.ForeignKey("users.user_id"), unique=True), _created_at(), sa.CheckConstraint("role IN ('admin', 'user')", name="ck_accounts_role"), sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_accounts_status"), sa.UniqueConstraint("username"))
    op.create_table("households", _id(), sa.Column("name", sa.String(100), nullable=False), sa.Column("created_by_account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False), _created_at())
    op.create_table("household_members", _id(), _household_id(), sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("role", sa.String(16), nullable=False), sa.Column("joined_at", sa.DateTime()), sa.CheckConstraint("role IN ('owner', 'admin', 'member', 'viewer')", name="ck_household_members_role"))
    op.create_table("household_preferences", _id(), _household_id(), sa.Column("key", sa.String(100), nullable=False), sa.Column("value", sa.Text(), nullable=False), sa.Column("confirmed_by_account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False), sa.Column("updated_at", sa.DateTime()), sa.UniqueConstraint("household_id", "key", name="uq_household_preference_key"))
    op.create_table("invitations", _id(), _household_id(), sa.Column("invited_account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False), sa.Column("invited_by_account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False), sa.Column("role", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False), _created_at(), sa.Column("accepted_at", sa.DateTime()), sa.CheckConstraint("role IN ('admin', 'member', 'viewer')", name="ck_invitations_role"), sa.CheckConstraint("status IN ('pending', 'accepted', 'revoked')", name="ck_invitations_status"))

    op.create_table("llm_endpoints", _id(), sa.Column("protocol", sa.String(32), nullable=False), sa.Column("display_name", sa.String(100), nullable=False), sa.Column("base_url", sa.String(500), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False))
    op.create_table("llm_credentials", sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("llm_endpoints.id", ondelete="CASCADE"), primary_key=True), sa.Column("ciphertext", sa.LargeBinary(), nullable=False), sa.Column("nonce", sa.LargeBinary(), nullable=False), sa.Column("key_version", sa.Integer(), nullable=False), sa.Column("last_four", sa.String(4), nullable=False))
    op.create_table("llm_models", _id(), sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("llm_endpoints.id", ondelete="CASCADE"), nullable=False), sa.Column("model_name", sa.String(200), nullable=False), sa.Column("model_type", sa.String(16), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False))
    op.create_table("model_purpose_bindings", sa.Column("purpose", sa.String(32), primary_key=True), sa.Column("model_id", sa.String(36), sa.ForeignKey("llm_models.id"), nullable=False))
    op.create_table("model_health_checks", _id(), sa.Column("model_id", sa.String(36), sa.ForeignKey("llm_models.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(32), nullable=False), _created_at())
    op.create_table("model_grants", _id(), _household_id(), _created_at(), sa.Column("model_id", sa.String(36), sa.ForeignKey("llm_models.id"), nullable=False))
    op.create_table("usage_quotas", _id(), _household_id(), _created_at(), sa.Column("monthly_limit", sa.Integer(), nullable=False))
    op.create_table("tool_definitions", _id(), sa.Column("key", sa.String(100), nullable=False, unique=True), sa.Column("risk_level", sa.String(32), nullable=False))
    op.create_table("tool_permissions", _id(), _household_id(), _created_at(), sa.Column("tool_id", sa.String(36), sa.ForeignKey("tool_definitions.id"), nullable=False))

    op.create_table("skills", _id(), _household_id(), _created_at(), sa.Column("name", sa.String(100), nullable=False))
    op.create_table("skill_versions", _id(), sa.Column("skill_id", sa.String(36), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("definition_json", sa.Text(), nullable=False), sa.Column("published_at", sa.DateTime()), sa.UniqueConstraint("skill_id", "version"))
    op.create_table("skill_knowledge_assets", _id(), sa.Column("skill_version_id", sa.String(36), sa.ForeignKey("skill_versions.id", ondelete="CASCADE"), nullable=False))
    op.create_table("agent_definitions", _id(), _household_id(), _created_at(), sa.Column("name", sa.String(100), nullable=False))
    op.create_table("agent_versions", _id(), sa.Column("agent_id", sa.String(36), sa.ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("definition_json", sa.Text(), nullable=False), sa.Column("published_at", sa.DateTime()), sa.UniqueConstraint("agent_id", "version"))
    op.create_table("agent_skill_bindings", _id(), sa.Column("agent_version_id", sa.String(36), sa.ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False), sa.Column("skill_version_id", sa.String(36), sa.ForeignKey("skill_versions.id"), nullable=False))
    op.create_table("agent_tool_bindings", _id(), sa.Column("agent_version_id", sa.String(36), sa.ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False), sa.Column("tool_id", sa.String(36), sa.ForeignKey("tool_definitions.id"), nullable=False))
    op.create_table("agent_deployments", _id(), _household_id(), _created_at(), sa.Column("agent_version_id", sa.String(36), sa.ForeignKey("agent_versions.id"), nullable=False))
    op.create_table("agent_installations", _id(), _household_id(), _created_at(), sa.Column("deployment_id", sa.String(36), sa.ForeignKey("agent_deployments.id"), nullable=False))
    op.create_table("installation_permissions", _id(), sa.Column("installation_id", sa.String(36), sa.ForeignKey("agent_installations.id", ondelete="CASCADE"), nullable=False), sa.Column("tool_id", sa.String(36), sa.ForeignKey("tool_definitions.id"), nullable=False))
    op.create_table("favorites", _id(), _household_id(), _created_at(), sa.Column("deployment_id", sa.String(36), sa.ForeignKey("agent_deployments.id"), nullable=False))
    op.create_table("creator_drafts", _id(), _household_id(), _created_at(), sa.Column("draft_type", sa.String(16), nullable=False))
    op.create_table("review_submissions", _id(), sa.Column("agent_version_id", sa.String(36), sa.ForeignKey("agent_versions.id"), nullable=False))
    op.create_table("review_checks", _id(), sa.Column("submission_id", sa.String(36), sa.ForeignKey("review_submissions.id", ondelete="CASCADE"), nullable=False))
    op.create_table("review_decisions", _id(), sa.Column("submission_id", sa.String(36), sa.ForeignKey("review_submissions.id", ondelete="CASCADE"), nullable=False))
    op.create_table("reports", _id(), _household_id(), _created_at(), sa.Column("deployment_id", sa.String(36), sa.ForeignKey("agent_deployments.id"), nullable=False))
    op.create_table("agent_runs", _id(), _household_id(), _created_at(), sa.Column("deployment_id", sa.String(36), sa.ForeignKey("agent_deployments.id"), nullable=False))
    op.create_table("tool_executions", _id(), sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False), sa.Column("tool_id", sa.String(36), sa.ForeignKey("tool_definitions.id"), nullable=False))
    op.create_table("approval_requests", _id(), sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False))
    op.create_table("household_tasks", _id(), _household_id(), _created_at(), sa.Column("title", sa.String(200), nullable=False))
    op.create_table("task_schedules", _id(), sa.Column("task_id", sa.String(36), sa.ForeignKey("household_tasks.id", ondelete="CASCADE"), nullable=False))
    op.create_table("notifications", _id(), _household_id(), _created_at(), sa.Column("title", sa.String(200), nullable=False))
    op.create_table("notification_reads", _id(), sa.Column("notification_id", sa.String(36), sa.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False), sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False))

    op.create_table("devices", sa.Column("model_id", sa.String(64), primary_key=True), sa.Column("model_name", sa.String(200), nullable=False), sa.Column("category", sa.String(100), nullable=False), sa.Column("specs", sa.Text(), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("price", sa.Float(), nullable=False), sa.Column("features", sa.Text(), nullable=False))
    op.create_table("inventory", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("model_id", sa.String(64), sa.ForeignKey("devices.model_id"), nullable=False), sa.Column("warehouse", sa.String(100), nullable=False), sa.Column("stock", sa.Integer(), nullable=False), sa.Column("min_stock", sa.Integer(), nullable=False), sa.Column("updated_at", sa.DateTime()), sa.UniqueConstraint("model_id", "warehouse"))
    op.create_table("device_usage_records", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("user_id", sa.String(32), sa.ForeignKey("users.user_id"), nullable=False), sa.Column("month", sa.String(16), nullable=False), sa.Column("feature", sa.Text(), nullable=False), sa.Column("efficiency", sa.Text(), nullable=False), sa.Column("consumables", sa.Text(), nullable=False), sa.Column("comparison", sa.Text(), nullable=False), _created_at(), sa.UniqueConstraint("user_id", "month"))
    op.create_table("chat_sessions", _id(), sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False), sa.Column("title", sa.String(100), nullable=False), _created_at(), sa.Column("updated_at", sa.DateTime()))
    op.create_table("conversation_history", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("session_id", sa.String(36), sa.ForeignKey("chat_sessions.id"), nullable=False), sa.Column("user_id", sa.String(32), sa.ForeignKey("users.user_id")), sa.Column("role", sa.String(16), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("sentiment", sa.String(32)), sa.Column("sentiment_score", sa.Float()), sa.Column("feedback", sa.String(16)), _created_at(), sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_conversation_history_role"), sa.CheckConstraint("feedback IS NULL OR feedback IN ('like', 'dislike')", name="ck_conversation_history_feedback"))
    op.create_table("tickets", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False), sa.Column("session_id", sa.String(36), sa.ForeignKey("chat_sessions.id")), sa.Column("title", sa.String(200), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("emotion", sa.String(32)), sa.Column("priority", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("admin_reply", sa.Text()), _created_at(), sa.Column("resolved_at", sa.DateTime()), sa.CheckConstraint("priority IN ('low', 'normal', 'high')", name="ck_tickets_priority"), sa.CheckConstraint("status IN ('open', 'processing', 'resolved', 'closed')", name="ck_tickets_status"))
    op.create_table("knowledge_files", _id(), sa.Column("filename", sa.String(255), nullable=False), sa.Column("file_md5", sa.String(32), nullable=False, unique=True), sa.Column("file_type", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("accounts.id")), _created_at(), sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_knowledge_files_status"))
    op.create_table("faq_pairs", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("question", sa.Text(), nullable=False), sa.Column("answer", sa.Text(), nullable=False), sa.Column("source_file_id", sa.String(36), sa.ForeignKey("knowledge_files.id")), sa.Column("status", sa.String(16), nullable=False), _created_at(), sa.Column("reviewed_at", sa.DateTime()), sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_faq_pairs_status"))
    op.create_table("restock_notifications", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False), sa.Column("model_id", sa.String(64), sa.ForeignKey("devices.model_id"), nullable=False), sa.Column("status", sa.String(16), nullable=False), _created_at(), sa.CheckConstraint("status IN ('waiting', 'notified')", name="ck_restock_notifications_status"), sa.UniqueConstraint("account_id", "model_id", name="uq_restock_account_model"))


def downgrade() -> None:
    for table in reversed([
        "users", "accounts", "households", "household_members", "household_preferences", "invitations",
        "llm_endpoints", "llm_credentials", "llm_models", "model_purpose_bindings", "model_health_checks", "model_grants", "usage_quotas",
        "tool_definitions", "tool_permissions", "skills", "skill_versions", "skill_knowledge_assets", "agent_definitions", "agent_versions",
        "agent_skill_bindings", "agent_tool_bindings", "agent_deployments", "agent_installations", "installation_permissions", "favorites",
        "creator_drafts", "review_submissions", "review_checks", "review_decisions", "reports", "agent_runs", "tool_executions",
        "approval_requests", "household_tasks", "task_schedules", "notifications", "notification_reads", "devices", "inventory",
        "device_usage_records", "chat_sessions", "conversation_history", "tickets", "knowledge_files", "faq_pairs", "restock_notifications",
    ]):
        op.drop_table(table)
