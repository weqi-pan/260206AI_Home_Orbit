"""Official device agent Fix Round 1 security constraints."""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0010"
down_revision = "20260828_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    ambiguous = connection.execute(sa.text("""
        SELECT 1 FROM (
            SELECT t.id FROM tickets t LEFT JOIN household_members hm
              ON hm.account_id=t.account_id AND hm.household_id=t.household_id
             WHERE hm.id IS NULL
            UNION ALL
            SELECT r.id FROM restock_notifications r LEFT JOIN household_members hm
              ON hm.account_id=r.account_id AND hm.household_id=r.household_id
             WHERE hm.id IS NULL
        ) invalid LIMIT 1
    """)).first()
    if ambiguous:
        raise RuntimeError("DEVICE_LEGACY_MEMBERSHIP_REPAIR_REQUIRED")
    household_count = connection.execute(sa.text("SELECT COUNT(*) FROM households")).scalar() or 0
    device_count = connection.execute(sa.text("SELECT COUNT(*) FROM devices")).scalar() or 0
    if household_count > 1 and device_count:
        owners = [row[0] for row in connection.execute(
            sa.text("SELECT DISTINCT household_id FROM devices")).fetchall()]
        if len(owners) == 1:
            earliest = connection.execute(sa.text(
                "SELECT household_id FROM household_members ORDER BY joined_at, id LIMIT 1")).scalar()
            if owners[0] == earliest:
                raise RuntimeError("DEVICE_LEGACY_ATTRIBUTION_AMBIGUOUS")
    with op.batch_alter_table("knowledge_files", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.drop_constraint("uq_knowledge_files_file_md5", type_="unique")
    with op.batch_alter_table("household_members") as batch:
        batch.create_unique_constraint("uq_household_member_account_household", ["account_id", "household_id"])
    with op.batch_alter_table("tickets") as batch:
        batch.create_foreign_key("fk_ticket_account_household_member", "household_members",
                                 ["account_id", "household_id"], ["account_id", "household_id"])
    with op.batch_alter_table("restock_notifications") as batch:
        batch.create_foreign_key("fk_restock_account_household_member", "household_members",
                                 ["account_id", "household_id"], ["account_id", "household_id"])
    op.create_table("admin_action_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("principal_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id", ondelete="CASCADE")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_admin_action_audits_principal_id", "admin_action_audits", ["principal_id"])
    op.create_index("ix_admin_action_audits_household_id", "admin_action_audits", ["household_id"])


def downgrade() -> None:
    op.drop_table("admin_action_audits")
    with op.batch_alter_table("restock_notifications") as batch:
        batch.drop_constraint("fk_restock_account_household_member", type_="foreignkey")
    with op.batch_alter_table("tickets") as batch:
        batch.drop_constraint("fk_ticket_account_household_member", type_="foreignkey")
    with op.batch_alter_table("household_members") as batch:
        batch.drop_constraint("uq_household_member_account_household", type_="unique")
    with op.batch_alter_table("knowledge_files") as batch:
        batch.create_unique_constraint("uq_knowledge_files_file_md5", ["file_md5"])
