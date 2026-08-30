import json
import threading
import time
from types import SimpleNamespace

from sqlalchemy import select

from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, BaseChatTransport, configured_runtime


def test_embedding_build_uses_captured_bytes_when_source_changes_and_is_restored(client, app, monkeypatch):
    from backend.app.db.models import (
        Account, ActiveEmbeddingCollection, EmbeddingCollectionBuild, HouseholdMember, KnowledgeFile,
        ModelHealthCheck, ModelPurposeBinding,
    )

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    original = b"captured build bytes"
    uncaptured = b"mutated after capture"
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        _, old = configured_runtime(session, household_id, purpose="embedding")
        session.add(ActiveEmbeddingCollection(key="knowledge", collection_name="knowledge-old", model_id=old.id))
        _, new = configured_runtime(session, household_id, purpose="embedding-candidate")
        new.model_type = "embedding"
        new.capability_flags_json = json.dumps({"embedding": "passed"})
        health = session.scalar(select(ModelHealthCheck).where(ModelHealthCheck.model_id == new.id))
        health.capabilities_json = json.dumps({"embedding": "passed"})
        session.delete(session.get(ModelPurposeBinding, "embedding-candidate"))
        source = KnowledgeFile(household_id=household_id, filename="race.txt", file_md5="a" * 32, file_type=".txt", status="active")
        session.add(source)
        session.commit()
        old_id, new_id, source_id = old.id, new.id, source.id

    source_path = app.state.settings.knowledge_upload_path / f"{source_id}.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(original)
    built_text = []

    class Collection:
        def delete(self, **kwargs):
            pass

    class Store:
        def __init__(self):
            self._collection = Collection()

        def add_documents(self, documents, ids=None):
            built_text.extend(document.page_content for document in documents)
            source_path.write_bytes(original)

    app.state.embedding_store_factory = lambda name, model_id: Store()

    real_capture = __import__("backend.app.api.models", fromlist=["capture_embedding_sources"]).capture_embedding_sources
    capture_count = 0

    def mutate_after_initial_capture(*args, **kwargs):
        nonlocal capture_count
        snapshot = real_capture(*args, **kwargs)
        capture_count += 1
        if capture_count == 1:
            source_path.write_bytes(uncaptured)
        return snapshot

    monkeypatch.setattr("backend.app.api.models.capture_embedding_sources", mutate_after_initial_capture)
    response = client.put("/api/models/bindings/embedding", headers=headers,
                          json={"purpose": "embedding", "model_id": new_id})

    assert response.status_code == 200
    embedded_text = "".join(built_text)
    assert original.decode("utf-8") in embedded_text
    assert uncaptured.decode("utf-8") not in embedded_text
    with app.state.database.session_factory() as session:
        assert session.get(ActiveEmbeddingCollection, "knowledge").model_id == new_id
        assert session.get(ModelPurposeBinding, "embedding").model_id == new_id
        evidence = session.scalar(select(EmbeddingCollectionBuild).order_by(EmbeddingCollectionBuild.created_at.desc())).evidence_json
        assert original.decode("utf-8") not in evidence
        assert uncaptured.decode("utf-8") not in evidence
        assert old_id != new_id
    assert not list(app.state.settings.chroma_path.glob("embedding-build-*"))


def test_embedding_staging_is_cleaned_when_build_fails(client, app, monkeypatch):
    from backend.app.db.models import Account, HouseholdMember, KnowledgeFile, ModelHealthCheck, ModelPurposeBinding

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        _, model = configured_runtime(session, household_id, purpose="embedding-candidate")
        model.model_type = "embedding"
        model.capability_flags_json = json.dumps({"embedding": "passed"})
        health = session.scalar(select(ModelHealthCheck).where(ModelHealthCheck.model_id == model.id))
        health.capabilities_json = json.dumps({"embedding": "passed"})
        session.delete(session.get(ModelPurposeBinding, "embedding-candidate"))
        source = KnowledgeFile(household_id=household_id, filename="failure.txt", file_md5="b" * 32, file_type=".txt", status="active")
        session.add(source)
        session.commit()
        model_id, source_id = model.id, source.id

    source_path = app.state.settings.knowledge_upload_path / f"{source_id}.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("temporary staged content", encoding="utf-8")

    class Store:
        class Collection:
            def delete(self, **kwargs):
                pass

        _collection = Collection()

        def add_documents(self, documents, ids=None):
            raise RuntimeError("provider failed")

    app.state.embedding_store_factory = lambda name, model_id: Store()
    response = client.put("/api/models/bindings/embedding", headers=headers,
                          json={"purpose": "embedding", "model_id": model_id})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EMBEDDING_REBUILD_FAILED"
    assert not list(app.state.settings.chroma_path.glob("embedding-build-*"))


def test_capability_probe_fails_when_post_first_stream_ignores_close():
    from backend.app.models.adapters import LangChainChatTransport, ModelAdapter
    from backend.app.models.capabilities import CapabilityTester

    class StalledIterator:
        def __init__(self):
            self.first = True
            self.close_called = threading.Event()

        def __iter__(self):
            return self

        def __next__(self):
            if self.first:
                self.first = False
                return SimpleNamespace(content="first")
            threading.Event().wait(10)
            raise StopIteration

        def close(self):
            self.close_called.set()

    stalled = StalledIterator()
    calls = 0

    def stream(messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            return iter([SimpleNamespace(content="normal")])
        return stalled

    model = SimpleNamespace(
        stream=stream,
        invoke=lambda messages: SimpleNamespace(content="hello", tool_calls=[]),
        bind_tools=lambda tools: SimpleNamespace(invoke=lambda messages: SimpleNamespace(
            content="", tool_calls=[{"name": "echo", "args": {}}],
        )),
        with_structured_output=lambda schema, include_raw=True: SimpleNamespace(
            invoke=lambda messages: {"parsed": {"ok": True}},
        ),
        _runtime_cancel=lambda: None,
    )
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=LangChainChatTransport(model, close_timeout=0.25))

    started = time.monotonic()
    result = CapabilityTester(adapter, first_chunk_timeout=1.0, cancel_timeout=0.6).run_chat()
    elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert result.passed is False
    assert result.error_code == "CANCELLATION_FAILED"
    assert result.capabilities["cancellation"] == "failed"
    assert stalled.close_called.is_set()
