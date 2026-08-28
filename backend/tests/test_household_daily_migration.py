"""Phase 5 Alembic revision 20260828_0011 roundtrip tests (SQLite-compatible)."""
import os
import subprocess
import sys
from pathlib import Path


def _alembic(tmp_path, command, target, database_path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", command, target],
                          cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)


def test_phase5_revision_upgrades_and_roundtrips(tmp_path):
    from sqlalchemy import create_engine, inspect

    database_path = tmp_path / "phase5.db"
    upgraded = _alembic(tmp_path, "upgrade", "head", database_path)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "shopping_list_items" in inspector.get_table_names()
        assert "household_memos" in inspector.get_table_names()
        assert "household_preference_proposals" in inspector.get_table_names()
        assert "celery_tasks" in inspector.get_table_names()
        schedule_columns = {column["name"] for column in inspector.get_columns("task_schedules")}
        assert {"timezone", "interval_minutes", "next_run_at", "missed_run_policy", "catch_up_limit",
                "active", "last_run_at"} <= schedule_columns
        preference_columns = {column["name"] for column in inspector.get_columns("household_preferences")}
        assert {"category", "value_type"} <= preference_columns
        notification_columns = {column["name"] for column in inspector.get_columns("notifications")}
        assert {"body", "kind", "source_type", "source_id"} <= notification_columns
        with engine.begin() as connection:
            from sqlalchemy import text
            connection.execute(text("SELECT version_num FROM alembic_version"))
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "20260828_0011"
    finally:
        engine.dispose()

    downgraded = _alembic(tmp_path, "downgrade", "20260828_0010", database_path)
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "shopping_list_items" not in inspector.get_table_names()
        assert "celery_tasks" not in inspector.get_table_names()
        schedule_columns = {column["name"] for column in inspector.get_columns("task_schedules")}
        assert "interval_minutes" not in schedule_columns
    finally:
        engine.dispose()

    reupgraded = _alembic(tmp_path, "upgrade", "head", database_path)
    assert reupgraded.returncode == 0, reupgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "shopping_list_items" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_create_all_matches_revision_head_for_phase5_tables(settings):
    """create_all（SQLite 测试/预览模式）与迁移 head 在 Phase 5 表结构上一致。"""
    from sqlalchemy import create_engine, inspect

    from backend.app.db.models import Base
    from backend.app.db.session import REQUIRED_DATABASE_REVISION

    assert REQUIRED_DATABASE_REVISION == "20260828_0011"
    engine = create_engine(f"sqlite:///{settings.database_url.split('///')[-1]}")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    assert {"shopping_list_items", "household_memos", "household_preference_proposals",
            "celery_tasks"} <= set(inspector.get_table_names())
    engine.dispose()
