"""迁移 20260828_0017 的结构与回滚验证（SQLite 上跑 upgrade → downgrade → upgrade）。"""
import os
import subprocess
import sys
from pathlib import Path


def _alembic(command, target, database_path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", command, target],
        cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True,
    )


def _columns(engine, table):
    from sqlalchemy import inspect

    return {column["name"] for column in inspect(engine).get_columns(table)}


def test_auth_hardening_revision_roundtrips(tmp_path):
    from sqlalchemy import create_engine, inspect

    database_path = tmp_path / "auth-0017.db"
    upgraded = _alembic("upgrade", "head", database_path)
    assert upgraded.returncode == 0, upgraded.stderr

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"refresh_sessions", "login_attempts"} <= tables
        assert "token_version" in _columns(engine, "accounts")
        assert {"token_id", "family_id", "account_id", "expires_at", "used_at", "revoked_at",
                "replaced_by_token_id", "client_ip", "user_agent", "created_at"} <= _columns(engine, "refresh_sessions")
        assert {"scope", "key_hash", "succeeded", "created_at"} <= _columns(engine, "login_attempts")
    finally:
        engine.dispose()

    downgraded = _alembic("downgrade", "20260828_0016", database_path)
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert "refresh_sessions" not in tables
        assert "login_attempts" not in tables
        assert "token_version" not in _columns(engine, "accounts")
    finally:
        engine.dispose()

    reupgraded = _alembic("upgrade", "head", database_path)
    assert reupgraded.returncode == 0, reupgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        assert "refresh_sessions" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_required_revision_matches_auth_hardening_head(settings):
    from backend.app.db.session import REQUIRED_DATABASE_REVISION

    # 迁移文件与门禁必须同 commit：不一致时生产启动直接 RuntimeError
    assert REQUIRED_DATABASE_REVISION == "20260828_0017"


def test_create_all_matches_migration_shape(settings):
    """模型与迁移不能分叉：create_all 建出来的表要能通过同一批断言。

    测试套件走 `create_all()`、生产走 Alembic，两条路建出的结构必须一致 ——
    否则单测全绿而迁移上线后列缺失（或反之）。
    """
    from sqlalchemy import create_engine

    from backend.app.db.models import Base

    engine = create_engine(f"sqlite:///{settings.database_url.split('///')[-1]}")
    Base.metadata.create_all(engine)
    try:
        assert "token_version" in _columns(engine, "accounts")
        assert {"token_id", "family_id", "account_id", "expires_at", "used_at", "revoked_at",
                "replaced_by_token_id", "client_ip", "user_agent", "created_at"} <= _columns(engine, "refresh_sessions")
        assert {"scope", "key_hash", "succeeded", "created_at"} <= _columns(engine, "login_attempts")
    finally:
        engine.dispose()
