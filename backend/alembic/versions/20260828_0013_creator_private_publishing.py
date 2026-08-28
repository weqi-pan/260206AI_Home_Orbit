"""Phase 6 creator private publishing: draft lifecycle columns, creator knowledge
assets and sandbox trial reports."""
from alembic import op
import sqlalchemy as sa

revision = "20260828_0013"
down_revision = "20260828_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creator_drafts") as batch:
        batch.add_column(sa.Column("title", sa.String(200), nullable=False, server_default=""))
        batch.add_column(sa.Column("status", sa.String(20), nullable=False, server_default="draft"))
        batch.add_column(sa.Column("source_description", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("intent_card_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("last_validation_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("agent_definition_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("deployment_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("created_by_account_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch.create_unique_constraint("uq_creator_drafts_id_household", ["id", "household_id"])
        batch.create_check_constraint("ck_creator_drafts_status",
                                      "status IN ('draft', 'validated', 'private_published')")
    op.create_table("creator_knowledge_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.String(36), nullable=False),
        sa.Column("skill_key", sa.String(100), nullable=False, server_default=""),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(16), nullable=False),
        sa.Column("file_md5", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("scan_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("scan_reason", sa.String(300), nullable=True),
        sa.Column("uploaded_by_account_id", sa.String(36), sa.ForeignKey("accounts.id")),
        sa.Column("created_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["draft_id", "household_id"],
                                ["creator_drafts.id", "creator_drafts.household_id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_creator_knowledge_status"),
        sa.CheckConstraint("scan_status IN ('pending', 'passed', 'rejected')", name="ck_creator_knowledge_scan"),
        sa.UniqueConstraint("id", "household_id"),
        sa.UniqueConstraint("draft_id", "household_id", "file_md5", name="uq_creator_asset_draft_md5"),
    )
    op.create_index("ix_creator_knowledge_assets_household_id", "creator_knowledge_assets", ["household_id"])
    op.create_table("creator_trial_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("granted_tool_keys_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("cases_json", sa.Text(), nullable=True),
        sa.Column("agent_version_id", sa.String(36), nullable=True),
        sa.Column("deployment_id", sa.String(36), nullable=True),
        sa.Column("installation_id", sa.String(36), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["draft_id", "household_id"],
                                ["creator_drafts.id", "creator_drafts.household_id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')",
                           name="ck_creator_trial_status"),
        sa.UniqueConstraint("id", "household_id"),
    )
    op.create_index("ix_creator_trial_reports_household_id", "creator_trial_reports", ["household_id"])
    op.create_index("ix_creator_trial_reports_draft_id", "creator_trial_reports", ["draft_id"])


def downgrade() -> None:
    op.drop_index("ix_creator_trial_reports_draft_id", table_name="creator_trial_reports")
    op.drop_index("ix_creator_trial_reports_household_id", table_name="creator_trial_reports")
    op.drop_table("creator_trial_reports")
    op.drop_index("ix_creator_knowledge_assets_household_id", table_name="creator_knowledge_assets")
    op.drop_table("creator_knowledge_assets")
    with op.batch_alter_table("creator_drafts") as batch:
        batch.drop_constraint("ck_creator_drafts_status", type_="check")
        batch.drop_constraint("uq_creator_drafts_id_household", type_="unique")
        batch.drop_column("updated_at")
        batch.drop_column("created_by_account_id")
        batch.drop_column("deployment_id")
        batch.drop_column("agent_definition_id")
        batch.drop_column("last_validation_json")
        batch.drop_column("intent_card_json")
        batch.drop_column("source_description")
        batch.drop_column("status")
        batch.drop_column("title")
