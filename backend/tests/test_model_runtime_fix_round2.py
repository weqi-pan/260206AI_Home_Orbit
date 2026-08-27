import base64
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime


def test_embedding_bind_fails_when_any_active_source_is_unresolved_and_records_audit_evidence(client, app, monkeypatch, tmp_path):
    from backend.app.db.models import (
        Account, ActiveEmbeddingCollection, EmbeddingCollectionBuild, HouseholdMember,
        KnowledgeFile, ModelPurposeBinding,
    )

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        _, old = configured_runtime(session, household_id, purpose="embedding")
        session.add(ActiveEmbeddingCollection(key="knowledge", collection_name="knowledge-old", model_id=old.id))
        _, new = configured_runtime(session, household_id, purpose="embedding-next")
        new.model_type = "embedding"; new.capability_flags_json = json.dumps({"embedding": "passed"})
        health = session.scalar(select(__import__('backend.app.db.models', fromlist=['ModelHealthCheck']).ModelHealthCheck)
                                .where(__import__('backend.app.db.models', fromlist=['ModelHealthCheck']).ModelHealthCheck.model_id == new.id))
        health.capabilities_json = json.dumps({"embedding": "passed"})
        session.delete(session.get(ModelPurposeBinding, "embedding-next"))
        valid = KnowledgeFile(filename="valid.txt", file_md5="1" * 32, file_type=".txt", uploaded_by=account.id, status="active")
        missing = KnowledgeFile(filename="missing.txt", file_md5="2" * 32, file_type=".txt", uploaded_by=account.id, status="active")
        session.add_all([valid, missing]); session.flush()
        valid_path = Path(app.state.settings.knowledge_upload_path) / f"{valid.id}.txt"
        valid_path.parent.mkdir(parents=True, exist_ok=True); valid_path.write_text("valid", encoding="utf-8")
        old_id, new_id, valid_id, missing_id = old.id, new.id, valid.id, missing.id
        session.commit()

    calls = []
    app.state.embedding_store_factory = lambda name, model_id: calls.append(name) or SimpleNamespace()
    response = client.put("/api/models/bindings/embedding", headers=headers,
                          json={"purpose": "embedding", "model_id": new_id})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EMBEDDING_SOURCE_INCOMPLETE"
    assert calls == []
    with app.state.database.session_factory() as session:
        assert session.get(ActiveEmbeddingCollection, "knowledge").model_id == old_id
        assert session.get(ModelPurposeBinding, "embedding").model_id == old_id
        build = session.scalar(select(EmbeddingCollectionBuild).order_by(EmbeddingCollectionBuild.created_at.desc()))
        evidence = json.loads(build.evidence_json)
        assert build.status == "failed"
        assert evidence["expected_file_count"] == evidence["included_file_count"] + len(evidence["missing_file_ids"])
        assert evidence["expected_faq_count"] == evidence["included_faq_count"] == len(evidence["included_faq_ids"])
        assert valid_id in evidence["included_file_ids"]
        assert missing_id in evidence["missing_file_ids"]
        assert "valid" not in json.dumps(evidence)


def test_stalled_production_stream_can_be_cancelled_promptly_and_closes_underlying_iterator():
    from backend.app.models.adapters import LangChainChatTransport, ModelAdapter

    class BlockingIterator:
        def __init__(self): self.release = threading.Event(); self.closed = threading.Event(); self.count = 0
        def __iter__(self): return self
        def __next__(self):
            self.count += 1
            if self.count == 1: return SimpleNamespace(content="first")
            self.release.wait(5)
            if self.closed.is_set(): raise StopIteration
            return SimpleNamespace(content="late")
        def close(self): self.closed.set(); self.release.set()

    iterator = BlockingIterator()
    client_closed = threading.Event()
    model = SimpleNamespace(stream=lambda messages: iterator, _runtime_cancel=lambda: client_closed.set())
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=LangChainChatTransport(model))
    cancel = threading.Event()
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(adapter.stream_text([{"role": "user", "content": "x"}], cancel)), daemon=True)
    worker.start()
    deadline = time.monotonic() + 1
    while iterator.count < 2 and time.monotonic() < deadline: time.sleep(0.01)
    cancel.set()
    worker.join(timeout=0.5)
    assert worker.is_alive() is False
    assert iterator.closed.is_set()
    assert client_closed.is_set()
    assert outcome[0].text == "first"


def test_runtime_snapshot_is_recursively_immutable_and_detached_from_source():
    from backend.app.models.runtime import ModelRuntimeSnapshot

    source = {"capabilities": {"generation": "passed"}, "stops": ["a"], "modes": {"safe"}}
    snapshot = ModelRuntimeSnapshot("primary_chat", "m", "e", 1, source)
    source["capabilities"]["generation"] = "failed"
    source["stops"].append("b")
    source["modes"].add("unsafe")
    assert snapshot.profile["capabilities"]["generation"] == "passed"
    assert snapshot.profile["stops"] == ("a",)
    assert snapshot.profile["modes"] == frozenset({"safe"})
    with pytest.raises(TypeError): snapshot.profile["capabilities"]["generation"] = "failed"
    with pytest.raises(AttributeError): snapshot.profile["stops"].append("b")


@pytest.mark.parametrize("payload", [
    {"enabled": None}, {"requests_per_minute": None}, {"temperature": None},
    {"requests_per_minute": 0}, {"max_tokens": 0}, {"agent_token_budget": 0},
])
def test_model_patch_rejects_null_and_invalid_domain_values(client, app, payload):
    from backend.app.db.models import LlmEndpoint, LlmModel

    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Patch", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", max_tokens=64,
                         requests_per_minute=10, enabled=True)
        session.add(model); session.flush(); model_id = model.id
    response = client.patch(f"/api/models/profiles/{model_id}", headers=headers, json=payload)
    assert response.status_code == 422


def test_model_patch_allows_clearing_agent_budget_only_with_positive_max_token_fallback(client, app):
    from backend.app.db.models import LlmEndpoint, LlmModel

    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Budget", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", max_tokens=64,
                         agent_token_budget=32, requests_per_minute=10, enabled=True)
        session.add(model); session.flush(); model_id = model.id
    response = client.patch(f"/api/models/profiles/{model_id}", headers=headers, json={"agent_token_budget": None})
    assert response.status_code == 200
    assert response.json()["agent_token_budget"] is None


def test_chat_profile_create_rejects_missing_effective_request_budget(client, app):
    from backend.app.db.models import LlmEndpoint

    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="No budget", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush(); endpoint_id = endpoint.id
    response = client.post("/api/models/profiles", headers=headers, json={
        "endpoint_id": endpoint_id, "model_name": "chat", "model_type": "chat", "temperature": 0.2,
        "max_tokens": None, "agent_token_budget": None, "requests_per_minute": 10, "enabled": True,
    })
    assert response.status_code == 422


def test_unreadable_active_source_preflight_fails_before_store_creation(client, app, monkeypatch):
    from backend.app.db.models import Account, ActiveEmbeddingCollection, HouseholdMember, KnowledgeFile, ModelPurposeBinding
    import backend.app.api.knowledge as knowledge_api

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        _, old = configured_runtime(session, household_id, purpose="embedding")
        session.add(ActiveEmbeddingCollection(key="knowledge", collection_name="knowledge-old", model_id=old.id))
        _, new = configured_runtime(session, household_id, purpose="embedding-new")
        new.model_type = "embedding"; new.capability_flags_json = json.dumps({"embedding": "passed"})
        health = session.scalar(select(__import__('backend.app.db.models', fromlist=['ModelHealthCheck']).ModelHealthCheck)
                                .where(__import__('backend.app.db.models', fromlist=['ModelHealthCheck']).ModelHealthCheck.model_id == new.id))
        health.capabilities_json = json.dumps({"embedding": "passed"})
        session.delete(session.get(ModelPurposeBinding, "embedding-new"))
        item = KnowledgeFile(filename="unreadable.txt", file_md5="3" * 32, file_type=".txt", uploaded_by=account.id, status="active")
        session.add(item); session.commit(); new_id = new.id; unreadable_id = item.id
    class UnreadablePath:
        suffix = ".txt"
        def is_file(self): return True
        def open(self, *args, **kwargs): raise PermissionError("secret path")
    original = knowledge_api._knowledge_file_path
    monkeypatch.setattr(knowledge_api, "_knowledge_file_path", lambda settings, row: UnreadablePath() if row.id == unreadable_id else original(settings, row))
    calls = []
    app.state.embedding_store_factory = lambda name, model_id: calls.append(name)
    response = client.put("/api/models/bindings/embedding", headers=headers,
                          json={"purpose": "embedding", "model_id": new_id})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EMBEDDING_SOURCE_INCOMPLETE"
    assert calls == []


def test_auxiliary_execution_rejects_missing_effective_budget_before_reservation(settings, monkeypatch):
    from backend.app.models.execution import BudgetedModel
    from backend.app.models.runtime import ModelRuntimeSnapshot
    import backend.app.models.execution as execution_module

    resolved = SimpleNamespace(snapshot=ModelRuntimeSnapshot("auxiliary_generation", "m", "e", 1,
                                                              {"agent_token_budget": None, "max_tokens": None}))
    class Runtime:
        def __init__(self, session): pass
        def resolve(self, purpose, household): return resolved
        def build_chat_model(self, runtime): raise AssertionError("provider must not run")
    monkeypatch.setattr(execution_module, "ModelRuntimeService", Runtime)
    @contextmanager
    def session_factory():
        yield SimpleNamespace()
    with pytest.raises(RuntimeError) as exc:
        BudgetedModel(session_factory, "h", "auxiliary_generation").invoke([])
    assert str(exc.value) == "MODEL_REQUEST_BUDGET_NOT_CONFIGURED"
