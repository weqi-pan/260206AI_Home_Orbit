import base64
import json
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime


def test_embedding_source_change_during_build_marks_stale_and_retains_pointer_and_binding(client, app, monkeypatch):
    from backend.app.db.models import (
        Account, ActiveEmbeddingCollection, EmbeddingCollectionBuild, FaqPair,
        HouseholdMember, ModelHealthCheck, ModelPurposeBinding,
    )

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        _, old = configured_runtime(session, household_id, purpose="embedding")
        session.add(ActiveEmbeddingCollection(key="knowledge", collection_name="knowledge-old", model_id=old.id))
        _, new = configured_runtime(session, household_id, purpose="embedding-candidate")
        new.model_type = "embedding"; new.capability_flags_json = json.dumps({"embedding": "passed"})
        health = session.scalar(select(ModelHealthCheck).where(ModelHealthCheck.model_id == new.id))
        health.capabilities_json = json.dumps({"embedding": "passed"})
        session.delete(session.get(ModelPurposeBinding, "embedding-candidate"))
        faq = FaqPair(question="before", answer="answer", status="approved", reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        session.add(faq); session.commit()
        old_id, new_id, faq_id = old.id, new.id, faq.id

    mutated = threading.Event()
    class Collection:
        def delete(self, **kwargs): pass
    class Store:
        def __init__(self): self._collection = Collection()
        def add_documents(self, documents, ids=None):
            if not mutated.is_set():
                with app.state.database.session_factory.begin() as other:
                    other.get(FaqPair, faq_id).question = "after"
                mutated.set()
    app.state.embedding_store_factory = lambda name, model_id: Store()

    response = client.put("/api/models/bindings/embedding", headers=headers,
                          json={"purpose": "embedding", "model_id": new_id})
    assert mutated.is_set()
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EMBEDDING_SOURCE_SNAPSHOT_STALE"
    with app.state.database.session_factory() as session:
        assert session.get(ActiveEmbeddingCollection, "knowledge").model_id == old_id
        assert session.get(ModelPurposeBinding, "embedding").model_id == old_id
        build = session.scalar(select(EmbeddingCollectionBuild).order_by(EmbeddingCollectionBuild.created_at.desc()))
        assert build.status == "failed"
        assert build.error_code == "EMBEDDING_SOURCE_SNAPSHOT_STALE"
        evidence = json.loads(build.evidence_json)
        assert evidence["source_snapshot_token"]
        assert evidence["final_source_snapshot_token"]
        assert evidence["source_snapshot_token"] != evidence["final_source_snapshot_token"]


class StallingIterator:
    def __init__(self, first_chunk, close_blocks=False):
        self.first_chunk = first_chunk
        self.close_blocks = close_blocks
        self.release = threading.Event()
        self.close_called = threading.Event()
        self.emitted = 0

    def __iter__(self): return self

    def __next__(self):
        if self.first_chunk and self.emitted == 0:
            self.emitted += 1
            return SimpleNamespace(content="first")
        self.release.wait(10)
        raise StopIteration

    def close(self):
        self.close_called.set()
        if self.close_blocks:
            self.release.wait(10)
        else:
            self.release.set()


@pytest.mark.parametrize("first_chunk", [False, True])
def test_stream_cancellation_is_bounded_before_or_after_first_chunk(first_chunk):
    from backend.app.models.adapters import LangChainChatTransport, ModelAdapter

    iterator = StallingIterator(first_chunk=first_chunk)
    runtime_closed = threading.Event()
    model = SimpleNamespace(stream=lambda messages: iterator, _runtime_cancel=lambda: (runtime_closed.set(), iterator.release.set()))
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=LangChainChatTransport(model))
    cancel = threading.Event(); result = []
    worker = threading.Thread(target=lambda: result.append(adapter.stream_text([], cancel)), daemon=True)
    started = time.monotonic(); worker.start(); time.sleep(0.05); cancel.set(); worker.join(timeout=0.6)
    elapsed = time.monotonic() - started
    assert worker.is_alive() is False
    assert elapsed < 0.6
    assert runtime_closed.is_set()
    assert iterator.close_called.is_set()
    assert result[0].text == ("first" if first_chunk else "")


def test_blocking_close_never_blocks_caller_and_capability_fails_closed():
    from backend.app.models.adapters import LangChainChatTransport, ModelAdapter

    iterator = StallingIterator(first_chunk=False, close_blocks=True)
    model = SimpleNamespace(stream=lambda messages: iterator, _runtime_cancel=lambda: None)
    adapter = ModelAdapter("openai_compatible", "chat", chat_transport=LangChainChatTransport(model))
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        adapter.cancel_active_stream([], threading.Event())
    elapsed = time.monotonic() - started
    assert elapsed < 0.8
    assert str(exc.value) == "STREAM_CANCELLATION_TIMEOUT"
    assert iterator.close_called.is_set()
    assert not any(thread.name.startswith("model-stream-") and not thread.daemon for thread in threading.enumerate())
