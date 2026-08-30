"""Fresh Alembic chains must support notification-read home queries."""
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect


def _alembic(command: str, target: str, database_path: Path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", command, target],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
    )


def test_fresh_sqlite_chain_serves_home_after_demo_login(settings, tmp_path):
    from backend.app.core.config import Settings
    from backend.app.db.seed import seed_database
    from backend.app.db.session import create_database
    from backend.app.main import create_app

    database_path = tmp_path / "notification-read-regression.db"
    upgraded = _alembic("upgrade", "head", database_path)
    assert upgraded.returncode == 0, upgraded.stderr

    migration_settings = Settings(
        app_mode="preview",
        database_url=f"sqlite:///{database_path}",
        jwt_secret=settings.jwt_secret,
        admin_initial_password=settings.admin_initial_password,
        demo_initial_password=settings.demo_initial_password,
        usage_csv_path=settings.usage_csv_path,
        knowledge_path=settings.knowledge_path,
        knowledge_upload_path=tmp_path / "knowledge",
        chroma_path=tmp_path / "chroma",
    )
    database = create_database(migration_settings)
    try:
        notification_read_columns = {
            column["name"] for column in inspect(database.engine).get_columns("notification_reads")
        }
        seed_database(database.session_factory, migration_settings)
    finally:
        database.engine.dispose()

    with TestClient(create_app(migration_settings), raise_server_exceptions=False) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "demo", "password": settings.demo_initial_password},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        orbit = client.get("/api/home/orbit", headers=headers)
        today = client.get("/api/home/today", headers=headers)

    downgraded = _alembic("downgrade", "20260828_0015", database_path)
    assert downgraded.returncode == 0, downgraded.stderr
    downgrade_database = create_database(migration_settings)
    try:
        downgraded_columns = {
            column["name"] for column in inspect(downgrade_database.engine).get_columns("notification_reads")
        }
    finally:
        downgrade_database.engine.dispose()

    assert {
        "notification_reads.created_at": "created_at" in notification_read_columns,
        "login": login.status_code,
        "orbit": orbit.status_code,
        "today": today.status_code,
        "downgrade_removed_created_at": "created_at" not in downgraded_columns,
    } == {
        "notification_reads.created_at": True,
        "login": 200,
        "orbit": 200,
        "today": 200,
        "downgrade_removed_created_at": True,
    }
