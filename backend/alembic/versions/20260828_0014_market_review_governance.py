"""Phase 7 market & review governance: public review submission lifecycle,
pre-check evidence, advisory risk summary, admin decisions, marketplace
entries/pinned installs, favorites and reports."""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0014"
down_revision = "20260828_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("market_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_household_id", sa.String(36),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_definition_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("category", sa.String(32), nullable=False, server_default="生活"),
        sa.Column("official", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("current_version_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("install_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("taken_down_at", sa.DateTime(), nullable=True),
        sa.Column("takedown_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.CheckConstraint("status IN ('published', 'deprecated')", name="ck_market_entries_status"),
    )
    op.create_index("ix_market_entries_source_household_id", "market_entries", ["source_household_id"])
    op.create_table("market_installs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_id", sa.String(36),
                  sa.ForeignKey("market_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("deployment_id", sa.String(36), nullable=False),
        sa.Column("agent_version_id", sa.String(36), nullable=False),
        sa.Column("pinned_version_number", sa.Integer(), nullable=False),
        sa.Column("installed_by_account_id", sa.String(36), sa.ForeignKey("accounts.id")),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("upgraded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_market_installs_household_id", "market_installs", ["household_id"])
    op.create_index("ix_market_installs_entry_id", "market_installs", ["entry_id"])

    with op.batch_alter_table("creator_drafts") as batch:
        batch.drop_constraint("ck_creator_drafts_status", type_="check")
        batch.create_check_constraint(
            "ck_creator_drafts_status",
            "status IN ('draft', 'validated', 'private_published', 'review_pending',"
            " 'public_published', 'deprecated')")

    with op.batch_alter_table("review_submissions") as batch:
        batch.add_column(sa.Column("draft_id", sa.String(36), nullable=False, server_default=""))
        batch.add_column(sa.Column("name", sa.String(100), nullable=False, server_default=""))
        batch.add_column(sa.Column("description", sa.String(500), nullable=False, server_default=""))
        batch.add_column(sa.Column("category", sa.String(32), nullable=False, server_default="生活"))
        batch.add_column(sa.Column("status", sa.String(20), nullable=False, server_default="precheck_pending"))
        batch.add_column(sa.Column("submitted_by_account_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("precheck_status", sa.String(16), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("precheck_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("precheck_attempt", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("precheck_finished_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("risk_summary_status", sa.String(16), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("risk_summary_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("entry_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("decided_by_account_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("decided_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("decision_reason", sa.String(500), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime()))
        batch.add_column(sa.Column("updated_at", sa.DateTime()))
        batch.create_foreign_key("fk_review_submissions_submitter", "accounts",
                                 ["submitted_by_account_id"], ["id"])
        batch.create_foreign_key("fk_review_submissions_entry", "market_entries", ["entry_id"], ["id"])
        batch.create_foreign_key("fk_review_submissions_decider", "accounts",
                                 ["decided_by_account_id"], ["id"])
        batch.create_check_constraint(
            "ck_review_submissions_status",
            "status IN ('precheck_pending', 'precheck_failed', 'review_pending', 'returned',"
            " 'rejected', 'approved')")
        batch.create_check_constraint("ck_review_submissions_precheck",
                                      "precheck_status IN ('pending', 'passed', 'failed')")
        batch.create_check_constraint("ck_review_submissions_risk_summary",
                                      "risk_summary_status IN ('pending', 'done', 'unavailable')")
    op.create_index("ix_review_submissions_draft_id", "review_submissions", ["draft_id"])

    with op.batch_alter_table("review_checks") as batch:
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("codes_json", sa.Text(), nullable=False))
        batch.add_column(sa.Column("evidence_json", sa.Text(), nullable=False))
        batch.add_column(sa.Column("created_at", sa.DateTime()))
    op.create_index("ix_review_checks_submission_id", "review_checks", ["submission_id"])

    with op.batch_alter_table("review_decisions") as batch:
        batch.add_column(sa.Column("action", sa.String(16), nullable=False, server_default="approve"))
        batch.add_column(sa.Column("reason", sa.String(500), nullable=False, server_default=""))
        batch.add_column(sa.Column("decided_by_account_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime()))
        batch.create_foreign_key("fk_review_decisions_decider", "accounts",
                                 ["decided_by_account_id"], ["id"])
    op.create_index("ix_review_decisions_submission_id", "review_decisions", ["submission_id"])

    with op.batch_alter_table("favorites") as batch:
        batch.alter_column("deployment_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("entry_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_favorites_entry", "market_entries", ["entry_id"], ["id"])

    with op.batch_alter_table("reports") as batch:
        batch.alter_column("deployment_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("entry_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("reporter_account_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("reason", sa.String(500), nullable=False, server_default=""))
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="open"))
        batch.add_column(sa.Column("handled_by_account_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("handled_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("resolution_note", sa.String(500), nullable=True))
        batch.create_foreign_key("fk_reports_entry", "market_entries", ["entry_id"], ["id"])
        batch.create_foreign_key("fk_reports_reporter", "accounts", ["reporter_account_id"], ["id"])
        batch.create_foreign_key("fk_reports_handler", "accounts", ["handled_by_account_id"], ["id"])
        batch.create_check_constraint("ck_reports_status", "status IN ('open', 'resolved', 'dismissed')")
    op.create_index("ix_reports_entry_id", "reports", ["entry_id"])


def downgrade() -> None:
    op.drop_index("ix_reports_entry_id", table_name="reports")
    with op.batch_alter_table("reports") as batch:
        batch.drop_constraint("ck_reports_status", type_="check")
        batch.drop_constraint("fk_reports_handler", type_="foreignkey")
        batch.drop_constraint("fk_reports_reporter", type_="foreignkey")
        batch.drop_constraint("fk_reports_entry", type_="foreignkey")
        batch.drop_column("resolution_note")
        batch.drop_column("handled_at")
        batch.drop_column("handled_by_account_id")
        batch.drop_column("status")
        batch.drop_column("reason")
        batch.drop_column("reporter_account_id")
        batch.drop_column("entry_id")
        batch.alter_column("deployment_id", existing_type=sa.String(36), nullable=False)
    with op.batch_alter_table("favorites") as batch:
        batch.drop_constraint("fk_favorites_entry", type_="foreignkey")
        batch.drop_column("entry_id")
        batch.alter_column("deployment_id", existing_type=sa.String(36), nullable=False)
    op.drop_index("ix_review_decisions_submission_id", table_name="review_decisions")
    with op.batch_alter_table("review_decisions") as batch:
        batch.drop_constraint("fk_review_decisions_decider", type_="foreignkey")
        batch.drop_column("created_at")
        batch.drop_column("decided_by_account_id")
        batch.drop_column("reason")
        batch.drop_column("action")
    op.drop_index("ix_review_checks_submission_id", table_name="review_checks")
    with op.batch_alter_table("review_checks") as batch:
        batch.drop_column("created_at")
        batch.drop_column("evidence_json")
        batch.drop_column("codes_json")
        batch.drop_column("passed")
        batch.drop_column("attempt")
    op.drop_index("ix_review_submissions_draft_id", table_name="review_submissions")
    with op.batch_alter_table("review_submissions") as batch:
        batch.drop_constraint("ck_review_submissions_risk_summary", type_="check")
        batch.drop_constraint("ck_review_submissions_precheck", type_="check")
        batch.drop_constraint("ck_review_submissions_status", type_="check")
        batch.drop_constraint("fk_review_submissions_decider", type_="foreignkey")
        batch.drop_constraint("fk_review_submissions_entry", type_="foreignkey")
        batch.drop_constraint("fk_review_submissions_submitter", type_="foreignkey")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
        batch.drop_column("decision_reason")
        batch.drop_column("decided_at")
        batch.drop_column("decided_by_account_id")
        batch.drop_column("entry_id")
        batch.drop_column("risk_summary_json")
        batch.drop_column("risk_summary_status")
        batch.drop_column("precheck_finished_at")
        batch.drop_column("precheck_attempt")
        batch.drop_column("precheck_json")
        batch.drop_column("precheck_status")
        batch.drop_column("submitted_by_account_id")
        batch.drop_column("status")
        batch.drop_column("category")
        batch.drop_column("description")
        batch.drop_column("name")
        batch.drop_column("draft_id")
    with op.batch_alter_table("creator_drafts") as batch:
        batch.drop_constraint("ck_creator_drafts_status", type_="check")
        batch.create_check_constraint("ck_creator_drafts_status",
                                      "status IN ('draft', 'validated', 'private_published')")
    op.drop_index("ix_market_installs_entry_id", table_name="market_installs")
    op.drop_index("ix_market_installs_household_id", table_name="market_installs")
    op.drop_table("market_installs")
    op.drop_index("ix_market_entries_source_household_id", table_name="market_entries")
    op.drop_table("market_entries")
