"""Household-isolate the official device agent domain."""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0009"
down_revision = "20260827_0008"
branch_labels = None
depends_on = None


def _household_for_account(connection, account_column):
    return f"(SELECT hm.household_id FROM household_members hm WHERE hm.account_id={account_column})"


def upgrade() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    uuid_default = "lower(hex(randomblob(16)))" if dialect == "sqlite" else "UUID()"

    with op.batch_alter_table("chat_sessions") as batch:
        batch.create_unique_constraint("uq_chat_session_household", ["id", "household_id"])

    for table in ("devices", "inventory", "device_usage_records", "tickets", "knowledge_files",
                  "faq_pairs", "restock_notifications"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("household_id", sa.String(36), nullable=True))
    naming = {"pk": "pk_%(table_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
              "uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("devices", naming_convention=naming) as batch:
        batch.add_column(sa.Column("id", sa.String(36), nullable=True))
    with op.batch_alter_table("inventory", naming_convention=naming) as batch:
        batch.add_column(sa.Column("device_id", sa.String(36), nullable=True))
    with op.batch_alter_table("restock_notifications") as batch:
        batch.add_column(sa.Column("device_id", sa.String(36), nullable=True))

    fallback = connection.execute(sa.text("SELECT household_id FROM household_members ORDER BY joined_at LIMIT 1")).scalar()
    source_count = sum(connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0 for table in (
        "devices", "inventory", "device_usage_records", "tickets", "knowledge_files", "faq_pairs", "restock_notifications"))
    if not fallback and source_count:
        raise RuntimeError("DEVICE_HOUSEHOLD_REPAIR_REQUIRED")
    connection.execute(sa.text("UPDATE devices SET household_id=:home, id=" + uuid_default), {"home": fallback})
    connection.execute(sa.text("UPDATE inventory SET household_id=:home"), {"home": fallback})
    connection.execute(sa.text("UPDATE device_usage_records SET household_id=:home"), {"home": fallback})
    connection.execute(sa.text(f"UPDATE tickets SET household_id={_household_for_account(connection, 'tickets.account_id')}"))
    connection.execute(sa.text(f"UPDATE restock_notifications SET household_id={_household_for_account(connection, 'restock_notifications.account_id')}"))
    connection.execute(sa.text("UPDATE knowledge_files SET household_id=:home"), {"home": fallback})
    connection.execute(sa.text("UPDATE faq_pairs SET household_id=:home"), {"home": fallback})
    connection.execute(sa.text("UPDATE inventory SET device_id=(SELECT d.id FROM devices d WHERE d.household_id=inventory.household_id AND d.model_id=inventory.model_id)"))
    connection.execute(sa.text("UPDATE restock_notifications SET device_id=(SELECT d.id FROM devices d WHERE d.household_id=restock_notifications.household_id AND d.model_id=restock_notifications.model_id)"))

    naming = {"pk": "pk_%(table_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
              "uq": "uq_%(table_name)s_%(column_0_name)s"}
    if dialect == "mysql":
        # MySQL 无法在仍有外键引用 devices 主键时 DROP PRIMARY KEY（错误 1553），
        # 且 0001 基线的外键是自动命名，须按实际名称先删除（SQLite 走 batch 重建路径不受影响）。
        inspector = sa.inspect(connection)
        for table in ("inventory", "restock_notifications"):
            for foreign_key in inspector.get_foreign_keys(table):
                if foreign_key["referred_table"] == "devices" and foreign_key["name"]:
                    op.drop_constraint(foreign_key["name"], table, type_="foreignkey")
        # 旧全局唯一 (model_id, warehouse) 阻止同型号进入多个家庭，按实际名称删除
        for unique in inspector.get_unique_constraints("inventory"):
            if unique["column_names"] == ["model_id", "warehouse"] and unique["name"]:
                op.drop_constraint(unique["name"], "inventory", type_="unique")
    inventory_source = None
    if dialect != "mysql":
        # SQLite 上 0001 基线的全局 UNIQUE(model_id, warehouse) 是无名 sqlite_autoindex，
        # 无法按名 DROP，且 batch 默认按反射重建会将其保留；改为以 copy_from 显式描述
        # 当前表结构（不含该遗留约束）完成重建，使迁移链终点与 ORM create_all 一致。
        source_metadata = sa.MetaData()
        sa.Table("devices", source_metadata, sa.Column("model_id", sa.String(64), primary_key=True))
        inventory_source = sa.Table(
            "inventory", source_metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("model_id", sa.String(64), nullable=False),
            sa.Column("warehouse", sa.String(100), nullable=False),
            sa.Column("stock", sa.Integer(), nullable=False),
            sa.Column("min_stock", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime()),
            sa.Column("household_id", sa.String(36), nullable=True),
            sa.Column("device_id", sa.String(36), nullable=True),
            sa.ForeignKeyConstraint(["model_id"], ["devices.model_id"], name="fk_inventory_model_id_devices"),
        )
    with op.batch_alter_table("devices", naming_convention=naming) as batch:
        batch.drop_constraint("pk_devices", type_="primary")
        batch.alter_column("id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("household_id", existing_type=sa.String(36), nullable=False)
        batch.create_primary_key("pk_devices", ["id"])
        batch.create_unique_constraint("uq_device_household_model", ["household_id", "model_id"])
        batch.create_unique_constraint("uq_device_id_household", ["id", "household_id"])
        batch.create_foreign_key("fk_device_household", "households", ["household_id"], ["id"], ondelete="CASCADE")
    with op.batch_alter_table("inventory", naming_convention=naming, copy_from=inventory_source) as batch:
        if dialect != "mysql":
            batch.drop_constraint("fk_inventory_model_id_devices", type_="foreignkey")
        batch.alter_column("household_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("device_id", existing_type=sa.String(36), nullable=False)
        batch.create_unique_constraint("uq_inventory_household_model_warehouse", ["household_id", "model_id", "warehouse"])
        batch.create_unique_constraint("uq_inventory_id_household", ["id", "household_id"])
        batch.create_foreign_key("fk_inventory_device_household", "devices", ["device_id", "household_id"], ["id", "household_id"])
    with op.batch_alter_table("device_usage_records") as batch:
        batch.alter_column("household_id", existing_type=sa.String(36), nullable=False)
        batch.create_unique_constraint("uq_usage_household_user_month", ["household_id", "user_id", "month"])
    for table in ("tickets", "knowledge_files", "faq_pairs", "restock_notifications"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("household_id", existing_type=sa.String(36), nullable=False)
            batch.create_foreign_key(f"fk_{table}_household", "households", ["household_id"], ["id"], ondelete="CASCADE")
    with op.batch_alter_table("knowledge_files") as batch:
        batch.create_unique_constraint("uq_knowledge_file_household_md5", ["household_id", "file_md5"])
        batch.create_unique_constraint("uq_knowledge_file_id_household", ["id", "household_id"])
    with op.batch_alter_table("tickets") as batch:
        batch.create_unique_constraint("uq_ticket_id_household", ["id", "household_id"])
        batch.create_foreign_key("fk_ticket_session_household", "chat_sessions", ["session_id", "household_id"], ["id", "household_id"])
    with op.batch_alter_table("faq_pairs") as batch:
        batch.create_foreign_key("fk_faq_file_household", "knowledge_files", ["source_file_id", "household_id"], ["id", "household_id"])
    with op.batch_alter_table("restock_notifications") as batch:
        batch.alter_column("device_id", existing_type=sa.String(36), nullable=False)
        batch.create_unique_constraint("uq_restock_household_account_model", ["household_id", "account_id", "model_id"])
        batch.create_foreign_key("fk_restock_device_household", "devices", ["device_id", "household_id"], ["id", "household_id"])


def downgrade() -> None:
    raise RuntimeError("OFFICIAL_DEVICE_AGENT_DOWNGRADE_REQUIRES_BACKUP_RESTORE")
