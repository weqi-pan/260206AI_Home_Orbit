"""Model runtime execution gates and revision validity."""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0003"
down_revision = "20260827_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_models") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("model_purpose_bindings") as batch:
        batch.add_column(sa.Column("endpoint_revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("model_revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("model_health_checks") as batch:
        batch.add_column(sa.Column("endpoint_revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("model_revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("model_usage") as batch:
        batch.add_column(sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="reserved"))


def downgrade() -> None:
    with op.batch_alter_table("model_usage") as batch:
        batch.drop_column("status")
        batch.drop_column("reserved_tokens")
    with op.batch_alter_table("model_health_checks") as batch:
        batch.drop_column("model_revision")
        batch.drop_column("endpoint_revision")
    with op.batch_alter_table("model_purpose_bindings") as batch:
        batch.drop_column("model_revision")
        batch.drop_column("endpoint_revision")
    with op.batch_alter_table("llm_models") as batch:
        batch.drop_column("revision")
