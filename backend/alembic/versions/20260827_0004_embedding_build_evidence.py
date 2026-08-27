"""Embedding rebuild completeness evidence."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0004"
down_revision = "20260827_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("embedding_collection_builds") as batch:
        batch.add_column(sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("error_code", sa.String(64)))


def downgrade() -> None:
    with op.batch_alter_table("embedding_collection_builds") as batch:
        batch.drop_column("error_code")
        batch.drop_column("evidence_json")
