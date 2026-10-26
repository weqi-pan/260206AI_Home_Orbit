# -*- coding: utf-8 -*-
"""Phase 7 Alembic revision 20260828_0014 roundtrip tests (SQLite-compatible)."""
import os
import subprocess
import sys
from pathlib import Path


def _alembic(command, target, database_path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", command, target],
                          cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)


def test_phase7_revision_upgrades_and_roundtrips(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    database_path = tmp_path / "phase7.db"
    upgraded = _alembic("upgrade", "head", database_path)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "market_entries" in inspector.get_table_names()
        assert "market_installs" in inspector.get_table_names()
        submission_columns = {column["name"] for column in inspector.get_columns("review_submissions")}
        assert {"draft_id", "status", "name", "description", "category", "precheck_json", "precheck_status",
                "risk_summary_status", "risk_summary_json", "entry_id", "decision_reason"} <= submission_columns
        decision_columns = {column["name"] for column in inspector.get_columns("review_decisions")}
        assert {"action", "reason", "decided_by_account_id"} <= decision_columns
        report_columns = {column["name"] for column in inspector.get_columns("reports")}
        assert {"entry_id", "reason", "status", "reporter_account_id"} <= report_columns
        entry_columns = {column["name"] for column in inspector.get_columns("market_entries")}
        assert {"source_household_id", "current_version_id", "install_count", "official",
                "status"} <= entry_columns
        with engine.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "20260828_0017"
    finally:
        engine.dispose()

    downgraded = _alembic("downgrade", "20260828_0013", database_path)
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "market_entries" not in inspector.get_table_names()
        assert "market_installs" not in inspector.get_table_names()
        submission_columns = {column["name"] for column in inspector.get_columns("review_submissions")}
        assert "precheck_json" not in submission_columns
        assert "status" not in submission_columns
        report_columns = {column["name"] for column in inspector.get_columns("reports")}
        assert "entry_id" not in report_columns
    finally:
        engine.dispose()

    reupgraded = _alembic("upgrade", "head", database_path)
    assert reupgraded.returncode == 0, reupgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "market_entries" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_required_revision_consistent_with_phase7_head(settings):
    from backend.app.db.models import Base
    from backend.app.db.session import REQUIRED_DATABASE_REVISION

    assert REQUIRED_DATABASE_REVISION == "20260828_0017"
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{settings.database_url.split('///')[-1]}")
    Base.metadata.create_all(engine)
    try:
        inspector = inspect(engine)
        assert {"market_entries", "market_installs"} <= set(inspector.get_table_names())
        submission_columns = {column["name"] for column in inspector.get_columns("review_submissions")}
        assert {"status", "precheck_json", "risk_summary_json"} <= submission_columns
    finally:
        engine.dispose()
