"""Phase 5 household daily loop: shopping list, memos, preference proposals,
task/schedule/notification columns and Celery task records."""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0011"
down_revision = "20260828_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("shopping_list_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(32), nullable=False, server_default="份"),
        sa.Column("category", sa.String(64), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by_account_id", sa.String(36), sa.ForeignKey("accounts.id")),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("id", "household_id"),
    )
    op.create_index("ix_shopping_list_items_household_id", "shopping_list_items", ["household_id"])
    op.create_table("household_memos",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_account_id", sa.String(36), sa.ForeignKey("accounts.id")),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("id", "household_id"),
    )
    op.create_index("ix_household_memos_household_id", "household_memos", ["household_id"])
    op.create_table("household_preference_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("value_type", sa.String(16), nullable=False, server_default="string"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proposed_by_account_id", sa.String(36), sa.ForeignKey("accounts.id")),
        sa.Column("proposed_by_kind", sa.String(16), nullable=False, server_default="agent"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("confirmed_by_account_id", sa.String(36), sa.ForeignKey("accounts.id")),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("decided_at", sa.DateTime()),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="ck_preference_proposals_status"),
        sa.CheckConstraint("value_type IN ('string', 'number', 'boolean', 'json')", name="ck_preference_proposals_type"),
        sa.CheckConstraint("proposed_by_kind IN ('agent', 'member')", name="ck_preference_proposals_kind"),
        sa.UniqueConstraint("id", "household_id"),
    )
    op.create_index("ix_household_preference_proposals_household_id", "household_preference_proposals", ["household_id"])
    op.create_table("celery_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("household_id", sa.String(36)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail", sa.String(200)),
        sa.Column("next_retry_at", sa.DateTime()),
        sa.Column("audit_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.CheckConstraint("status IN ('pending', 'running', 'succeeded', 'failed', 'retry_scheduled', 'dead')",
                           name="ck_celery_tasks_status"),
        sa.UniqueConstraint("idempotency_key", name="uq_celery_tasks_idempotency_key"),
    )
    op.create_index("ix_celery_tasks_task_type", "celery_tasks", ["task_type"])
    op.create_index("ix_celery_tasks_household_id", "celery_tasks", ["household_id"])

    with op.batch_alter_table("household_preferences") as batch:
        batch.add_column(sa.Column("category", sa.String(64), nullable=False, server_default="general"))
        batch.add_column(sa.Column("value_type", sa.String(16), nullable=False, server_default="string"))
    with op.batch_alter_table("household_tasks") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=False))
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="open"))
        batch.add_column(sa.Column("due_at", sa.DateTime()))
        batch.add_column(sa.Column("created_by_account_id", sa.String(36)))
        batch.add_column(sa.Column("completed_at", sa.DateTime()))
        batch.add_column(sa.Column("completed_by_account_id", sa.String(36)))
        batch.add_column(sa.Column("updated_at", sa.DateTime()))
        batch.create_foreign_key("fk_household_tasks_created_by", "accounts",
                                 ["created_by_account_id"], ["id"])
        batch.create_foreign_key("fk_household_tasks_completed_by", "accounts",
                                 ["completed_by_account_id"], ["id"])
    with op.batch_alter_table("task_schedules") as batch:
        batch.add_column(sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"))
        batch.add_column(sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60"))
        batch.add_column(sa.Column("next_run_at", sa.DateTime()))
        batch.add_column(sa.Column("missed_run_policy", sa.String(16), nullable=False, server_default="skip"))
        batch.add_column(sa.Column("catch_up_limit", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")))
        batch.add_column(sa.Column("last_run_at", sa.DateTime()))
        batch.add_column(sa.Column("created_by_account_id", sa.String(36)))
        batch.add_column(sa.Column("created_at", sa.DateTime()))
        batch.create_foreign_key("fk_task_schedules_created_by", "accounts",
                                 ["created_by_account_id"], ["id"])
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("body", sa.Text(), nullable=False))
        batch.add_column(sa.Column("kind", sa.String(32), nullable=False, server_default="general"))
        batch.add_column(sa.Column("source_type", sa.String(32), nullable=False, server_default=""))
        batch.add_column(sa.Column("source_id", sa.String(100), nullable=False, server_default=""))
    with op.batch_alter_table("notification_reads") as batch:
        batch.add_column(sa.Column("read_at", sa.DateTime()))
        batch.create_unique_constraint("uq_notification_read_notification_account",
                                       ["notification_id", "account_id"])
        batch.create_foreign_key("fk_notification_read_account_household_member", "household_members",
                                 ["account_id", "household_id"], ["account_id", "household_id"])


def downgrade() -> None:
    with op.batch_alter_table("notification_reads") as batch:
        batch.drop_constraint("fk_notification_read_account_household_member", type_="foreignkey")
        batch.drop_constraint("uq_notification_read_notification_account", type_="unique")
        batch.drop_column("read_at")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_column("source_id")
        batch.drop_column("source_type")
        batch.drop_column("kind")
        batch.drop_column("body")
    with op.batch_alter_table("task_schedules") as batch:
        batch.drop_constraint("fk_task_schedules_created_by", type_="foreignkey")
        batch.drop_column("created_at")
        batch.drop_column("created_by_account_id")
        batch.drop_column("last_run_at")
        batch.drop_column("active")
        batch.drop_column("catch_up_limit")
        batch.drop_column("missed_run_policy")
        batch.drop_column("next_run_at")
        batch.drop_column("interval_minutes")
        batch.drop_column("timezone")
    with op.batch_alter_table("household_tasks") as batch:
        batch.drop_constraint("fk_household_tasks_completed_by", type_="foreignkey")
        batch.drop_constraint("fk_household_tasks_created_by", type_="foreignkey")
        batch.drop_column("updated_at")
        batch.drop_column("completed_by_account_id")
        batch.drop_column("completed_at")
        batch.drop_column("created_by_account_id")
        batch.drop_column("due_at")
        batch.drop_column("status")
        batch.drop_column("description")
    with op.batch_alter_table("household_preferences") as batch:
        batch.drop_column("value_type")
        batch.drop_column("category")
    op.drop_index("ix_celery_tasks_household_id", table_name="celery_tasks")
    op.drop_index("ix_celery_tasks_task_type", table_name="celery_tasks")
    op.drop_table("celery_tasks")
    op.drop_index("ix_household_preference_proposals_household_id", table_name="household_preference_proposals")
    op.drop_table("household_preference_proposals")
    op.drop_index("ix_household_memos_household_id", table_name="household_memos")
    op.drop_table("household_memos")
    op.drop_index("ix_shopping_list_items_household_id", table_name="shopping_list_items")
    op.drop_table("shopping_list_items")
