"""SQLite 迁移链端到端种子回归：head 结构必须支持多家庭同型号库存（0009 遗留全局唯一约束）。"""
import os
import subprocess
import sys
from pathlib import Path


def _upgrade_head(database_path: Path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
    )


def test_fresh_sqlite_chain_seeds_inventory_for_all_households(settings, tmp_path):
    """全新 SQLite 库跑完整迁移链后，seed_database 必须为两个家庭都写入同型号库存。"""
    from sqlalchemy import func, select

    from backend.app.core.config import Settings
    from backend.app.db.models import HouseholdMember, Inventory
    from backend.app.db.seed import INVENTORY, seed_database
    from backend.app.db.session import create_database

    database_path = tmp_path / "seed-regression.db"
    upgraded = _upgrade_head(database_path)
    assert upgraded.returncode == 0, upgraded.stderr

    migration_settings = Settings(
        app_mode="preview",
        database_url=f"sqlite:///{database_path}",
        jwt_secret=settings.jwt_secret,
        admin_initial_password=settings.admin_initial_password,
        demo_initial_password=settings.demo_initial_password,
        usage_csv_path=settings.usage_csv_path,
        knowledge_path=settings.knowledge_path,
    )
    database = create_database(migration_settings)
    try:
        seed_database(database.session_factory, migration_settings)

        with database.session_factory() as session:
            household_ids = sorted(session.scalars(select(HouseholdMember.household_id)).all())
            assert len(household_ids) == 2
            counts = dict(session.execute(
                select(Inventory.household_id, func.count()).group_by(Inventory.household_id)).all())
            assert counts == {household_id: len(INVENTORY) for household_id in household_ids}
            for model_id, warehouse, _stock in INVENTORY[:2]:
                owners = sorted(session.scalars(select(Inventory.household_id).where(
                    Inventory.model_id == model_id, Inventory.warehouse == warehouse)).all())
                assert owners == household_ids, (model_id, warehouse, owners)
    finally:
        database.engine.dispose()
