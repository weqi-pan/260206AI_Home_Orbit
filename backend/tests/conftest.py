import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / ".tmp" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def settings(tmp_path: Path):
    from backend.app.core.config import Settings

    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        sqlite_fallback_url=f"sqlite:///{tmp_path / 'fallback.db'}",
        jwt_secret="test-secret-with-sufficient-length",
        admin_initial_password="AdminTest@123",
        demo_initial_password="DemoTest@123",
        usage_csv_path=Path(__file__).parents[2] / "data" / "external" / "records.csv",
        knowledge_path=Path(__file__).parents[2] / "data",
    )


@pytest.fixture
def app(settings):
    from backend.app.main import create_app

    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def no_dashscope_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
