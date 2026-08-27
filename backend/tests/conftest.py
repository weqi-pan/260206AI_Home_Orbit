from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def settings(tmp_path: Path):
    from backend.app.core.config import Settings

    return Settings(
        app_mode="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        jwt_secret="test-secret-with-sufficient-length",
        admin_initial_password="AdminTest@123",
        demo_initial_password="DemoTest@123",
        usage_csv_path=Path(__file__).parents[2] / "data" / "external" / "records.csv",
        knowledge_path=Path(__file__).parents[2] / "data",
        knowledge_upload_path=tmp_path / "knowledge",
        chroma_path=tmp_path / "chroma",
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
def no_model_master_key(monkeypatch):
    monkeypatch.delenv("LLM_CREDENTIAL_MASTER_KEY", raising=False)
