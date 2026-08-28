import base64
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select


MASTER_KEY = base64.b64encode(bytes(range(32))).decode("ascii")
CHAT_CAPS = {name: "passed" for name in ("generation", "streaming", "cancellation", "tool_calling", "structured_output")}


class BaseChatTransport:
    def generate(self, *, model, messages, tools=None, structured_schema=None):
        if tools: return {"content": "", "tool_calls": [{"name": "echo", "args": {}}]}
        if structured_schema: return {"content": '{"ok":true}'}
        return {"content": "hello"}

    def stream(self, *, model, messages, cancel_event):
        yield {"content": "hello"}


def configured_runtime(session, household_id, *, purpose="primary_chat", grant=True, max_tokens=8, model_rpm=10, household_rpm=10, monthly=100):
    from backend.app.db.models import LlmCredential, LlmEndpoint, LlmModel, ModelGrant, ModelHealthCheck, ModelPurposeBinding, UsageQuota
    from backend.app.models.credentials import CredentialCipher

    endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://models.example/v1", enabled=True, revision=3)
    session.add(endpoint); session.flush()
    encrypted = CredentialCipher(MASTER_KEY).encrypt(endpoint.id, endpoint.protocol, 1, "sk-runtime-1234")
    session.add(LlmCredential(endpoint_id=endpoint.id, ciphertext=encrypted.ciphertext, nonce=encrypted.nonce, key_version=1, last_four="1234"))
    is_embedding = purpose == "embedding"
    caps = {"embedding": "passed"} if is_embedding else CHAT_CAPS
    model = LlmModel(endpoint_id=endpoint.id, model_name="embed" if is_embedding else "chat", model_type="embedding" if is_embedding else "chat", max_tokens=max_tokens,
                     agent_token_budget=max_tokens, requests_per_minute=model_rpm, enabled=True,
                     capability_flags_json=json.dumps(caps))
    session.add(model); session.flush()
    session.add(ModelHealthCheck(model_id=model.id, status="passed", capabilities_json=json.dumps(caps),
                                 evidence_json="{}", endpoint_revision=endpoint.revision, model_revision=model.revision))
    session.add(ModelPurposeBinding(purpose=purpose, model_id=model.id, config_revision=5,
                                    endpoint_revision=endpoint.revision, model_revision=model.revision))
    quota = session.scalar(select(UsageQuota).where(UsageQuota.household_id == household_id))
    if quota is None:
        session.add(UsageQuota(household_id=household_id, monthly_limit=monthly, requests_per_minute=household_rpm,
                               agent_token_budget=max_tokens))
    if grant:
        session.add(ModelGrant(household_id=household_id, model_id=model.id))
    session.commit()
    return endpoint, model


def test_runtime_requires_household_grant_and_current_evidence(settings, monkeypatch):
    from backend.app.db.models import Account, Household
    from backend.app.db.session import create_database
    from backend.app.models.runtime_service import ModelRuntimeError, ModelRuntimeService

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    database = create_database(settings); database.create_all()
    with database.session_factory() as session:
        owner = Account(username="gate-owner", password_hash="hash"); session.add(owner); session.flush()
        household = Household(name="Gate", created_by_account_id=owner.id); session.add(household); session.flush()
        _, model = configured_runtime(session, household.id, grant=False)
        with pytest.raises(ModelRuntimeError) as ungranted:
            ModelRuntimeService(session).resolve("primary_chat", household_id=household.id)
        assert ungranted.value.code == "MODEL_NOT_GRANTED"
        model.revision += 1; session.commit()
        with pytest.raises(ModelRuntimeError) as stale:
            ModelRuntimeService(session).resolve("primary_chat", household_id=household.id, require_grant=False)
        assert stale.value.code == "MODEL_CONFIGURATION_STALE"


def test_usage_reservation_fails_closed_without_household_quota(settings):
    from backend.app.db.models import Account, Household, LlmEndpoint, LlmModel
    from backend.app.db.session import create_database
    from backend.app.models.quotas import QuotaExceeded, QuotaService

    database = create_database(settings); database.create_all()
    with database.session_factory() as session:
        owner = Account(username="no-quota", password_hash="hash"); session.add(owner); session.flush()
        household = Household(name="No quota", created_by_account_id=owner.id); session.add(household)
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://models.example/v1", enabled=True)
        session.add(endpoint); session.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", enabled=True); session.add(model); session.commit()
        with pytest.raises(QuotaExceeded) as exc:
            QuotaService(session).reserve(household.id, model.id, 1)
        assert exc.value.code == "HOUSEHOLD_QUOTA_NOT_CONFIGURED"


def test_chat_reserves_grant_quota_and_records_usage_before_real_runner(client, app, monkeypatch):
    from backend.app.db.models import Account, HouseholdMember, ModelUsage

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "demo"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        configured_runtime(session, household_id)
        configured_runtime(session, household_id, purpose="embedding")

    class Runner:
        def bind_model(self, run_id, model): pass
        def stream(self, messages, context):
            assert context.model_snapshot["profile"]["max_tokens"] == 8
            yield {"event": "token", "data": {"content": "ok"}}

    runner = Runner()
    app.state.agent_runner = runner
    app.state.deployment_agent_runner = runner
    app.state.enforce_model_runtime_for_injected_runner = True
    app.state.chat_model_builder = lambda resolved: object()
    session_id = client.post("/api/chat/sessions", headers=headers, json={}).json()["id"]
    response = client.post(f"/api/chat/sessions/{session_id}/chat", headers=headers, json={"message": "run"})
    assert response.status_code == 200
    with app.state.database.session_factory() as session:
        usage = session.scalar(select(ModelUsage))
        assert usage.tokens == 8
        assert usage.status == "completed"


@pytest.mark.parametrize("grant,monthly,household_rpm,expected", [
    (False, 100, 10, "MODEL_NOT_GRANTED"),
    (True, 4, 10, "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"),
    (True, 100, 0, "HOUSEHOLD_RATE_LIMIT_EXCEEDED"),
])
def test_chat_real_boundary_rejects_before_provider(client, app, monkeypatch, grant, monthly, household_rpm, expected):
    from backend.app.db.models import Account, HouseholdMember

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "demo"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        configured_runtime(session, household_id, grant=grant, monthly=monthly, household_rpm=household_rpm)
        configured_runtime(session, household_id, purpose="embedding", grant=grant, monthly=monthly, household_rpm=household_rpm)
    calls = []
    app.state.chat_model_builder = lambda resolved: calls.append(resolved) or object()
    session_id = client.post("/api/chat/sessions", headers=headers, json={}).json()["id"]
    response = client.post(f"/api/chat/sessions/{session_id}/chat", headers=headers, json={"message": "blocked"})
    assert response.status_code == 429 if "EXCEEDED" in expected else 403
    assert response.json()["detail"]["code"] == expected
    assert calls == []


def test_clear_key_and_wrong_master_key_break_readiness_without_leak(client, app, monkeypatch):
    from backend.app.db.models import Account, HouseholdMember

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        endpoint, _ = configured_runtime(session, household_id)
        configured_runtime(session, household_id, purpose="embedding")
        endpoint_id = endpoint.id
    assert client.patch(f"/api/models/endpoints/{endpoint_id}", headers=headers, json={"clear_api_key": True}).status_code == 200
    status = client.get("/api/setup/status", headers=headers).json()
    assert status["ready"] is False
    assert "ciphertext" not in json.dumps(status)
    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", base64.b64encode(b"x" * 32).decode())
    status = client.get("/api/setup/status", headers=headers).json()
    assert status["ready"] is False
    assert "1234" not in json.dumps(status)


def test_endpoint_change_and_failed_retest_invalidate_new_runs(settings, monkeypatch):
    from backend.app.db.models import Account, Household
    from backend.app.db.session import create_database
    from backend.app.models.registry import ModelRegistry
    from backend.app.models.runtime_service import ModelRuntimeError, ModelRuntimeService

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    database = create_database(settings); database.create_all()
    with database.session_factory() as session:
        owner = Account(username="revision-owner", password_hash="hash"); session.add(owner); session.flush()
        household = Household(name="Revision", created_by_account_id=owner.id); session.add(household); session.flush()
        endpoint, model = configured_runtime(session, household.id)
        endpoint.base_url = "https://changed.example/v1"; endpoint.revision += 1; session.commit()
        with pytest.raises(ModelRuntimeError) as stale:
            ModelRuntimeService(session).resolve("primary_chat", household.id)
        assert stale.value.code == "MODEL_CONFIGURATION_STALE"
        ModelRegistry(session).record_capability_result(model.id, {"generation": "failed"}, error_code="GENERATION_FAILED")
        with pytest.raises(ModelRuntimeError) as failed:
            ModelRuntimeService(session).resolve("primary_chat", household.id)
        assert failed.value.code == "MODEL_CAPABILITY_NOT_VERIFIED"


def test_rebinding_is_rejected_before_outbound_connect():
    from backend.app.models.endpoint_security import PinnedNetworkBackend, UnsafeEndpointUrl

    calls = []
    backend = PinnedNetworkBackend(resolver=lambda host: ["127.0.0.1"], connector=lambda **kwargs: calls.append(kwargs))
    with pytest.raises(UnsafeEndpointUrl):
        backend.connect_tcp("models.example", 443, timeout=1)
    assert calls == []


def test_active_stream_cancellation_closes_and_stops_emission():
    from backend.app.models.adapters import ModelAdapter
    from backend.app.models.capabilities import CapabilityTester

    class Transport(BaseChatTransport):
        def __init__(self): self.closed = False; self.after_cancel = 0; self.stream_calls = 0
        def stream(self, *, model, messages, cancel_event):
            self.stream_calls += 1
            try:
                yield {"content": "first"}
                if cancel_event.is_set(): return
                if self.stream_calls > 1: self.after_cancel += 1
                yield {"content": "second"}
            finally: self.closed = True

    transport = Transport()
    result = CapabilityTester(ModelAdapter("openai_compatible", "chat", chat_transport=transport)).run_chat()
    assert result.capabilities["cancellation"] == "passed"
    assert transport.closed is True
    assert transport.after_cancel == 0


def test_capability_errors_are_allowlisted_and_never_persist_exception_text():
    from backend.app.models.adapters import ModelAdapter
    from backend.app.models.capabilities import CapabilityTester

    class SecretTransport(BaseChatTransport):
        def generate(self, **kwargs):
            raise RuntimeError("Authorization: Bearer sk-secret https://private.example body")

    result = CapabilityTester(ModelAdapter("openai_compatible", "chat", chat_transport=SecretTransport())).run_chat()
    assert result.error_code == "GENERATION_FAILED"
    assert "secret" not in json.dumps(result.__dict__)


def test_auxiliary_services_resolve_purpose_model_and_preserve_degradation():
    from backend.app.services.followup import FollowupService

    calls = []
    resolver = lambda purpose: calls.append(purpose) or SimpleNamespace(invoke=lambda messages: SimpleNamespace(content='["next"]'))
    assert FollowupService(model_resolver=resolver).generate("u", "a") == ["next"]
    assert calls == ["auxiliary_generation"]
    assert FollowupService(model_resolver=lambda purpose: (_ for _ in ()).throw(RuntimeError("offline"))).generate("u", "a") == []


def test_db_collection_build_switches_only_after_success_and_vector_reads_active(settings, tmp_path):
    from backend.app.db.models import Account, ActiveEmbeddingCollection, Household, LlmEndpoint, LlmModel
    from backend.app.db.session import create_database
    from backend.app.models.collections import DatabaseCollectionSwitchService
    from backend.rag.vector_store import VectorStoreService

    database = create_database(settings); database.create_all()
    stores = {}
    with database.session_factory() as session:
        owner = Account(username="vector-owner", password_hash="hash"); session.add(owner); session.flush()
        household = Household(name="Vector", created_by_account_id=owner.id); session.add(household)
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://models.example/v1", enabled=True)
        session.add(endpoint); session.flush()
        old = LlmModel(endpoint_id=endpoint.id, model_name="old", model_type="embedding", enabled=True)
        new = LlmModel(endpoint_id=endpoint.id, model_name="new", model_type="embedding", enabled=True)
        session.add_all([old, new]); session.flush()
        session.add(ActiveEmbeddingCollection(key="knowledge", collection_name="knowledge-old", model_id=old.id)); session.commit()
        switch = DatabaseCollectionSwitchService(session, store_factory=lambda name, model_id: stores.setdefault(name, SimpleNamespace(name=name)))
        build = switch.rebuild(new.id, lambda store: None)
        assert build.status == "active"
        assert session.get(ActiveEmbeddingCollection, "knowledge").collection_name == build.collection_name
        failed = switch.prepare(old.id)
        with pytest.raises(RuntimeError): switch.build(failed.id, lambda store: (_ for _ in ()).throw(RuntimeError("boom")))
        assert session.get(ActiveEmbeddingCollection, "knowledge").collection_name == build.collection_name
        vector = VectorStoreService(store_resolver=lambda: stores[session.get(ActiveEmbeddingCollection, "knowledge").collection_name])
        assert vector._get_store().name == build.collection_name


def test_vector_store_constructs_configured_openai_embedding_for_active_collection(settings, monkeypatch):
    from backend.app.db.models import Account, ActiveEmbeddingCollection, Household
    from backend.app.db.session import create_database
    from backend.app.models.runtime_service import ModelRuntimeService
    from backend.rag.vector_store import configured_vector_store

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    database = create_database(settings); database.create_all()
    built = []
    with database.session_factory() as session:
        owner = Account(username="embed-owner", password_hash="hash"); session.add(owner); session.flush()
        household = Household(name="Embed", created_by_account_id=owner.id); session.add(household); session.flush()
        _, model = configured_runtime(session, household.id, purpose="embedding")
        session.add(ActiveEmbeddingCollection(key="knowledge", collection_name="knowledge-active", model_id=model.id)); session.commit()
        service = configured_vector_store(
            session_factory=database.session_factory,
            persist_directory=settings.chroma_path,
            runtime_factory=lambda db: ModelRuntimeService(db),
            embedding_builder=lambda resolved: built.append(resolved.snapshot.model_id) or object(),
            store_factory=lambda name, embeddings, path: SimpleNamespace(name=name, embeddings=embeddings),
        )
        assert service._get_store().name == "knowledge-active"
        assert built == [model.id]


def test_embedding_binding_api_rebuilds_before_atomic_pointer_and_binding_switch(client, app, monkeypatch):
    from backend.app.db.models import Account, ActiveEmbeddingCollection, HouseholdMember, ModelPurposeBinding

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        _, old = configured_runtime(session, household_id, purpose="embedding")
        pointer = ActiveEmbeddingCollection(key="knowledge", collection_name="knowledge-old", model_id=old.id)
        session.add(pointer); session.commit()
        endpoint, new = configured_runtime(session, household_id, purpose="embedding-next")
        new.model_type = "embedding"; new.capability_flags_json = json.dumps({"embedding": "passed"})
        health = session.scalar(select(__import__('backend.app.db.models', fromlist=['ModelHealthCheck']).ModelHealthCheck)
                                .where(__import__('backend.app.db.models', fromlist=['ModelHealthCheck']).ModelHealthCheck.model_id == new.id))
        health.capabilities_json = json.dumps({"embedding": "passed"})
        session.delete(session.get(ModelPurposeBinding, "embedding-next")); session.commit()
        new_id = new.id

    rebuilt = []
    class Collection:
        def delete(self, **kwargs): rebuilt.append(kwargs)
    class Store:
        def __init__(self): self._collection = Collection()
        def add_documents(self, documents, ids=None): rebuilt.append((documents, ids))
    app.state.embedding_store_factory = lambda collection_name, model_id: Store()
    response = client.put("/api/models/bindings/embedding", headers=headers,
                          json={"purpose": "embedding", "model_id": new_id})
    assert response.status_code == 200, response.text
    with app.state.database.session_factory() as session:
        assert session.get(ModelPurposeBinding, "embedding").model_id == new_id
        assert session.get(ActiveEmbeddingCollection, "knowledge").model_id == new_id
    deletes = [item for item in rebuilt if isinstance(item, dict) and "$and" in item.get("where", {})]
    assert any(item["where"]["$and"][0] == {"household_id": household_id} for item in deletes)
    assert all(item["where"]["$and"][2] == {"source_type": {"$in": ["kb", "faq"]}} for item in deletes)
    assert len(deletes) >= 2


def test_anthropic_builder_uses_controlled_http_client_without_network():
    import httpx
    from backend.app.models.adapters import build_langchain_chat

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    model = build_langchain_chat("anthropic", api_key="key", model="claude-test", base_url="https://api.anthropic.com",
                                 timeout=30, max_retries=0, temperature=0, max_tokens=32, http_client=client)
    assert model.__dict__["_client"]._client is client


def test_production_runner_normalizes_block_text_and_tool_calls(monkeypatch):
    from langchain_core.messages import AIMessageChunk
    from backend.app.services.defaults import UnavailableAgentRunner
    import backend.agent.deep_agent as deep_agent_module

    class Agent:
        def stream(self, *args, **kwargs):
            message = AIMessageChunk(content=[{"type": "text", "text": "hello"}],
                                     tool_calls=[{"name": "echo", "args": {"value": "ok"}, "id": "call-1"}])
            yield "messages", (message, {})

    monkeypatch.setattr(deep_agent_module, "create_deep_agent", lambda: Agent())
    events = list(UnavailableAgentRunner().stream([], {}))
    assert events == [
        {"event": "token", "data": {"content": "hello"}},
        {"event": "tool", "data": {"name": "echo", "args": {"value": "ok"}, "id": "call-1", "type": "tool_call"}},
    ]
