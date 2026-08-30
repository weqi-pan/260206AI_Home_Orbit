"""Add the tenant timestamp expected by NotificationRead ORM queries."""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0016"
down_revision = "20260828_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_reads") as batch:
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notification_reads") as batch:
        batch.drop_column("created_at")
