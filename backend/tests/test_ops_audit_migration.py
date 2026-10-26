# -*- coding: utf-8 -*-
"""Phase 8 Alembic revision 20260828_0015 roundtrip tests: quota incident
table plus the indexes required by the audit/ops admin queries."""
import os
import subprocess
import sys
from pathlib import Path


def _alembic(command, target, database_path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", command, target],
                          cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)


def _index_names(engine, table):
    from sqlalchemy import inspect

    return {index["name"] for index in inspect(engine).get_indexes(table)}


def test_phase8_revision_upgrades_and_roundtrips(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    database_path = tmp_path / "phase8.db"
    upgraded = _alembic("upgrade", "head", database_path)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "quota_incidents" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("quota_incidents")}
        assert {"id", "household_id", "model_id", "code", "created_at"} <= columns
        for table, expected in {
            "agent_runs": {"ix_agent_runs_status_created", "ix_agent_runs_deployment_id", "ix_agent_runs_created_at"},
            "tool_executions": {"ix_tool_executions_run_id", "ix_tool_executions_status_started",
                                "ix_tool_executions_tool_id"},
            "model_usage": {"ix_model_usage_household_month", "ix_model_usage_model_id",
                            "ix_model_usage_status_created", "ix_model_usage_created_at"},
            "celery_tasks": {"ix_celery_tasks_status_created", "ix_celery_tasks_error_code"},
            "admin_action_audits": {"ix_admin_action_audits_action_created", "ix_admin_action_audits_resource",
                                    "ix_admin_action_audits_created_at"},
            "review_submissions": {"ix_review_submissions_status_created", "ix_review_submissions_entry_id"},
            "quota_incidents": {"ix_quota_incidents_household_id", "ix_quota_incidents_created_at"},
        }.items():
            assert expected <= _index_names(engine, table), (table, expected - _index_names(engine, table))
        with engine.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "20260828_0017"
    finally:
        engine.dispose()

    downgraded = _alembic("downgrade", "20260828_0014", database_path)
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "quota_incidents" not in inspector.get_table_names()
        assert "ix_agent_runs_status_created" not in _index_names(engine, "agent_runs")
        assert "ix_tool_executions_run_id" not in _index_names(engine, "tool_executions")
        assert "ix_model_usage_household_month" not in _index_names(engine, "model_usage")
        assert "ix_celery_tasks_status_created" not in _index_names(engine, "celery_tasks")
        assert "ix_admin_action_audits_action_created" not in _index_names(engine, "admin_action_audits")
        assert "ix_review_submissions_status_created" not in _index_names(engine, "review_submissions")
    finally:
        engine.dispose()

    reupgraded = _alembic("upgrade", "head", database_path)
    assert reupgraded.returncode == 0, reupgraded.stderr


def test_required_revision_consistent_with_phase8_head(settings):
    from sqlalchemy import create_engine, inspect

    from backend.app.db.models import Base
    from backend.app.db.session import REQUIRED_DATABASE_REVISION

    assert REQUIRED_DATABASE_REVISION == "20260828_0017"
    engine = create_engine(f"sqlite:///{settings.database_url.split('///')[-1]}")
    Base.metadata.create_all(engine)
    try:
        inspector = inspect(engine)
        assert "quota_incidents" in inspector.get_table_names()
        assert "ix_agent_runs_status_created" in _index_names(engine, "agent_runs")
    finally:
        engine.dispose()
