"""Multi-model runtime foundation."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0002"
down_revision = "20260827_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_endpoints") as batch:
        batch.add_column(sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="30"))
        batch.add_column(sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"))
        batch.add_column(sa.Column("disabled_reason", sa.String(64)))
    with op.batch_alter_table("llm_models") as batch:
        batch.add_column(sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"))
        batch.add_column(sa.Column("max_tokens", sa.Integer()))
        batch.add_column(sa.Column("embedding_dimensions", sa.Integer()))
        batch.add_column(sa.Column("use_responses_api", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("capability_flags_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("requests_per_minute", sa.Integer(), nullable=False, server_default="60"))
        batch.add_column(sa.Column("agent_token_budget", sa.Integer()))
    with op.batch_alter_table("model_purpose_bindings") as batch:
        batch.add_column(sa.Column("config_revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("model_health_checks") as batch:
        batch.add_column(sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("error_code", sa.String(64)))
    with op.batch_alter_table("usage_quotas") as batch:
        batch.add_column(sa.Column("requests_per_minute", sa.Integer(), nullable=False, server_default="60"))
        batch.add_column(sa.Column("agent_token_budget", sa.Integer()))
        batch.create_unique_constraint("uq_usage_quota_household", ["household_id"])
    with op.batch_alter_table("model_grants") as batch:
        batch.create_unique_constraint("uq_model_grant_household_model", ["household_id", "model_id"])
    op.create_table(
        "model_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(36), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_model_usage_household_id", "model_usage", ["household_id"])
    op.create_index("ix_model_usage_month", "model_usage", ["month"])
    op.create_table(
        "embedding_collection_builds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("llm_models.id"), nullable=False),
        sa.Column("collection_name", sa.String(200)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "active_embedding_collection",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("collection_name", sa.String(200), nullable=False),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("llm_models.id"), nullable=False),
        sa.Column("previous_collection_name", sa.String(200)),
    )


def downgrade() -> None:
    op.drop_table("active_embedding_collection")
    op.drop_table("embedding_collection_builds")
    op.drop_index("ix_model_usage_month", table_name="model_usage")
    op.drop_index("ix_model_usage_household_id", table_name="model_usage")
    op.drop_table("model_usage")
    with op.batch_alter_table("model_grants") as batch:
        batch.drop_constraint("uq_model_grant_household_model", type_="unique")
    with op.batch_alter_table("usage_quotas") as batch:
        batch.drop_constraint("uq_usage_quota_household", type_="unique")
        batch.drop_column("agent_token_budget")
        batch.drop_column("requests_per_minute")
    with op.batch_alter_table("model_health_checks") as batch:
        batch.drop_column("error_code")
        batch.drop_column("evidence_json")
        batch.drop_column("capabilities_json")
    with op.batch_alter_table("model_purpose_bindings") as batch:
        batch.drop_column("config_revision")
    with op.batch_alter_table("llm_models") as batch:
        for column in ["agent_token_budget", "requests_per_minute", "capability_flags_json", "use_responses_api",
                       "embedding_dimensions", "max_tokens", "temperature"]:
            batch.drop_column(column)
    with op.batch_alter_table("llm_endpoints") as batch:
        batch.drop_column("disabled_reason")
        batch.drop_column("max_retries")
        batch.drop_column("timeout_seconds")
