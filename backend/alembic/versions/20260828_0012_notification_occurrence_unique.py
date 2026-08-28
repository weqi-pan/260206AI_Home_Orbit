"""Fix Round 1: reminder occurrence notifications are DB-unique per household.

Adds a nullable occurrence_key column (NULL for every non-task_schedule
notification kind, exploiting NULL-unique semantics like authority_slot) plus a
unique constraint on (household_id, occurrence_key), and backfills existing
task_schedule rows from source_id so historical dedupe keeps working.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0012"
down_revision = "20260828_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("occurrence_key", sa.String(200), nullable=True))
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE notifications SET occurrence_key = source_id"
        " WHERE source_type = 'task_schedule' AND source_id <> ''"))
    with op.batch_alter_table("notifications") as batch:
        batch.create_unique_constraint("uq_notifications_household_occurrence",
                                       ["household_id", "occurrence_key"])


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("uq_notifications_household_occurrence", type_="unique")
        batch.drop_column("occurrence_key")
