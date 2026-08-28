# -*- coding: utf-8 -*-
"""Phase 6 Alembic revision 20260828_0013 roundtrip tests (SQLite-compatible)."""
import os
import subprocess
import sys
from pathlib import Path


def _alembic(tmp_path, command, target, database_path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", command, target],
                          cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)


def test_phase6_revision_upgrades_and_roundtrips(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    database_path = tmp_path / "phase6.db"
    upgraded = _alembic(tmp_path, "upgrade", "head", database_path)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "creator_knowledge_assets" in inspector.get_table_names()
        assert "creator_trial_reports" in inspector.get_table_names()
        draft_columns = {column["name"] for column in inspector.get_columns("creator_drafts")}
        assert {"title", "status", "source_description", "intent_card_json", "last_validation_json",
                "agent_definition_id", "deployment_id", "created_by_account_id", "updated_at"} <= draft_columns
        with engine.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "20260828_0015"
    finally:
        engine.dispose()

    downgraded = _alembic(tmp_path, "downgrade", "20260828_0012", database_path)
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "creator_knowledge_assets" not in inspector.get_table_names()
        assert "creator_trial_reports" not in inspector.get_table_names()
        draft_columns = {column["name"] for column in inspector.get_columns("creator_drafts")}
        assert "intent_card_json" not in draft_columns
        assert "status" not in draft_columns
    finally:
        engine.dispose()

    reupgraded = _alembic(tmp_path, "upgrade", "head", database_path)
    assert reupgraded.returncode == 0, reupgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "creator_knowledge_assets" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_required_revision_consistent_with_phase6_head(settings):
    from backend.app.db.models import Base
    from backend.app.db.session import REQUIRED_DATABASE_REVISION

    assert REQUIRED_DATABASE_REVISION == "20260828_0015"
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{settings.database_url.split('///')[-1]}")
    Base.metadata.create_all(engine)
    try:
        inspector = inspect(engine)
        assert {"creator_knowledge_assets", "creator_trial_reports"} <= set(inspector.get_table_names())
        draft_columns = {column["name"] for column in inspector.get_columns("creator_drafts")}
        assert {"title", "status", "intent_card_json"} <= draft_columns
    finally:
        engine.dispose()
