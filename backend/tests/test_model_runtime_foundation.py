import base64
import json
from types import SimpleNamespace

import pytest


MASTER_KEY = base64.b64encode(bytes(range(32))).decode("ascii")


class FakeChatTransport:
    def generate(self, *, model, messages, tools=None, structured_schema=None):
        if tools:
            return {"content": "", "tool_calls": [{"name": "echo", "args": {"value": "ok"}}]}
        if structured_schema:
            return {"content": [{"type": "text", "text": '{"ok":true}'}]}
        return {"content": [{"type": "text", "text": "hello"}]}

    def stream(self, *, model, messages, cancel_event):
        yield {"content": [{"type": "text", "text": "hel"}]}
        if cancel_event.is_set():
            return
        yield {"content": "lo"}


class FakeEmbeddingTransport:
    def embed(self, *, model, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_aes_gcm_credentials_use_endpoint_protocol_version_aad_and_hide_plaintext():
    from backend.app.models.credentials import CredentialCipher, InvalidMasterKey

    cipher = CredentialCipher(MASTER_KEY)
    first = cipher.encrypt("ep-1", "openai_compatible", 1, "sk-secret-1234")
    second = cipher.encrypt("ep-1", "openai_compatible", 1, "sk-secret-1234")

    assert first.nonce != second.nonce
    assert first.last_four == "1234"
    assert b"sk-secret" not in first.ciphertext
    assert cipher.decrypt("ep-1", "openai_compatible", first) == "sk-secret-1234"
    with pytest.raises(Exception):
        cipher.decrypt("ep-2", "openai_compatible", first)
    with pytest.raises(InvalidMasterKey):
        CredentialCipher("not-base64")
    with pytest.raises(InvalidMasterKey):
        CredentialCipher(base64.b64encode(b"short").decode("ascii"))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/v1",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/v1",
    "http://10.0.0.8/v1",
    "file:///etc/passwd",
])
def test_endpoint_url_policy_rejects_local_metadata_private_and_non_http(url):
    from backend.app.models.endpoint_security import UnsafeEndpointUrl, validate_endpoint_url

    with pytest.raises(UnsafeEndpointUrl):
        validate_endpoint_url(url, resolver=lambda host: [host])


def test_endpoint_url_policy_checks_all_dns_answers_to_resist_rebinding():
    from backend.app.models.endpoint_security import UnsafeEndpointUrl, validate_endpoint_url

    with pytest.raises(UnsafeEndpointUrl):
        validate_endpoint_url("https://models.example/v1", resolver=lambda host: ["8.8.8.8", "127.0.0.1"])
    assert validate_endpoint_url("https://models.example/v1", resolver=lambda host: ["8.8.8.8"]) == "https://models.example/v1"


def test_adapter_normalizes_openai_and_anthropic_content_and_exercises_capabilities():
    from backend.app.models.adapters import ModelAdapter
    from backend.app.models.capabilities import CapabilityTester

    adapter = ModelAdapter("openai_compatible", "demo", chat_transport=FakeChatTransport(), embedding_transport=FakeEmbeddingTransport())
    evidence = CapabilityTester(adapter).run_chat()
    embedding = CapabilityTester(adapter).run_embedding(expected_dimensions=3)

    assert evidence.passed is True
    assert evidence.capabilities == {
        "generation": "passed",
        "streaming": "passed",
        "cancellation": "passed",
        "tool_calling": "passed",
        "structured_output": "passed",
    }
    assert [event.type for event in evidence.events] == ["text", "text", "tool", "structured"]
    assert "".join(event.text or "" for event in evidence.events if event.type == "text") == "hellohello"
    assert embedding.passed is True
    assert embedding.dimensions == 3


def test_capability_failure_gates_binding_and_does_not_accept_generation_only(settings):
    from backend.app.db.platform_models import LlmEndpoint, LlmModel
    from backend.app.db.session import create_database
    from backend.app.models.registry import BindingRejected, ModelRegistry

    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Fake", base_url="https://models.example/v1", enabled=True)
        session.add(endpoint)
        session.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", enabled=True)
        session.add(model)
        session.commit()
        registry = ModelRegistry(session)
        registry.record_capability_result(model.id, {"generation": "passed", "streaming": "failed"}, error_code="STREAM_FAILED")

        with pytest.raises(BindingRejected) as exc:
            registry.bind("primary_chat", model.id)
        assert exc.value.code == "MODEL_CAPABILITY_NOT_VERIFIED"


def test_anthropic_embedding_profile_is_rejected(settings):
    from backend.app.db.platform_models import LlmEndpoint
    from backend.app.db.session import create_database
    from backend.app.models.registry import ModelRegistry, RegistryValidationError

    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        endpoint = LlmEndpoint(protocol="anthropic", display_name="Claude", base_url="https://api.anthropic.com", enabled=True)
        session.add(endpoint)
        session.commit()
        with pytest.raises(RegistryValidationError) as exc:
            ModelRegistry(session).create_model(endpoint.id, "embed", "embedding")
        assert exc.value.code == "ANTHROPIC_CHAT_ONLY"


def test_quota_is_household_isolated_and_returns_stable_over_limit_codes(settings):
    from backend.app.db.household_models import Household
    from backend.app.db.models import Account, LlmEndpoint, LlmModel
    from backend.app.db.session import create_database
    from backend.app.models.quotas import QuotaExceeded, QuotaService

    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        owner = Account(username="quota-owner", password_hash="hash")
        session.add(owner)
        session.flush()
        first = Household(name="First", created_by_account_id=owner.id)
        second = Household(name="Second", created_by_account_id=owner.id)
        session.add_all([first, second])
        session.flush()
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Quota", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", enabled=True)
        session.add(model); session.flush()
        quota = QuotaService(session)
        quota.configure(first.id, monthly_limit=10, requests_per_minute=10, agent_token_budget=6)
        quota.consume(first.id, model_id=model.id, tokens=6)
        with pytest.raises(QuotaExceeded) as monthly:
            quota.consume(first.id, model_id=model.id, tokens=5)
        assert monthly.value.code == "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"
        assert quota.current_usage(second.id) == 0


def test_runtime_snapshot_is_immutable_after_binding_revision_changes():
    from backend.app.models.runtime import ModelRuntimeSnapshot

    snapshot = ModelRuntimeSnapshot(purpose="primary_chat", model_id="m1", endpoint_id="e1", config_revision=7, profile={"temperature": 0.2})
    with pytest.raises((TypeError, AttributeError)):
        snapshot.config_revision = 8
    assert snapshot.profile["temperature"] == 0.2


def test_collection_switch_builds_new_version_then_atomically_activates_and_retains_old():
    from backend.app.models.collections import CollectionSwitchService, InMemoryCollectionCatalog

    catalog = InMemoryCollectionCatalog(active="knowledge-v1")
    service = CollectionSwitchService(catalog)
    pending = service.prepare("embedding-model-v2")
    assert catalog.active == "knowledge-v1"
    assert pending.status == "building"
    service.complete(pending.id, built_collection="knowledge-v2")
    assert catalog.active == "knowledge-v2"
    assert catalog.retained == ["knowledge-v1"]


def test_model_admin_api_masks_keys_preserves_omitted_key_and_requires_admin(client, monkeypatch):
    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    created = client.post("/api/models/endpoints", headers=headers, json={
        "protocol": "openai_compatible", "display_name": "Gateway", "base_url": "https://api.openai.com/v1",
        "api_key": "sk-api-9999", "timeout_seconds": 30, "max_retries": 2, "enabled": True,
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_api_key"] is True
    assert body["masked_api_key"] == "****9999"
    assert "ciphertext" not in json.dumps(body)
    endpoint_id = body["id"]

    updated = client.patch(f"/api/models/endpoints/{endpoint_id}", headers=headers, json={"display_name": "Gateway 2"})
    assert updated.status_code == 200
    assert updated.json()["has_api_key"] is True
    cleared = client.patch(f"/api/models/endpoints/{endpoint_id}", headers=headers, json={"clear_api_key": True})
    assert cleared.json()["has_api_key"] is False


def test_setup_readiness_requires_valid_master_key_and_verified_required_bindings(client, monkeypatch):
    monkeypatch.delenv("LLM_CREDENTIAL_MASTER_KEY", raising=False)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    response = client.get("/api/setup/status", headers={"Authorization": f"Bearer {login['access_token']}"})
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["master_key_valid"] is False
    assert response.json()["error_code"] == "MODEL_NOT_CONFIGURED"


def test_runtime_resolves_verified_binding_decrypts_credential_and_freezes_profile(settings, monkeypatch):
    from backend.app.db.models import LlmCredential, LlmEndpoint, LlmModel, ModelHealthCheck, ModelPurposeBinding
    from backend.app.db.session import create_database
    from backend.app.models.credentials import CredentialCipher
    from backend.app.models.runtime_service import ModelRuntimeService

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://api.openai.com/v1", enabled=True, revision=4)
        session.add(endpoint); session.flush()
        encrypted = CredentialCipher(MASTER_KEY).encrypt(endpoint.id, endpoint.protocol, 1, "sk-runtime-1234")
        session.add(LlmCredential(endpoint_id=endpoint.id, ciphertext=encrypted.ciphertext, nonce=encrypted.nonce, key_version=1, last_four=encrypted.last_four))
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", temperature=0.4, enabled=True,
                         capability_flags_json=json.dumps({name: "passed" for name in ("generation", "streaming", "cancellation", "tool_calling", "structured_output")}))
        session.add(model); session.flush()
        session.add(ModelHealthCheck(model_id=model.id, status="passed", capabilities_json=model.capability_flags_json,
                                     evidence_json="{}", endpoint_revision=endpoint.revision, model_revision=model.revision))
        session.add(ModelPurposeBinding(purpose="primary_chat", model_id=model.id, config_revision=9,
                                        endpoint_revision=endpoint.revision, model_revision=model.revision)); session.commit()

        resolved = ModelRuntimeService(session).resolve("primary_chat", require_grant=False)
        snapshot = resolved.snapshot
        model.temperature = 1.2; session.commit()

        assert resolved.api_key == "sk-runtime-1234"
        assert snapshot.config_revision == 9
        assert snapshot.profile["temperature"] == 0.4


def test_invalid_master_key_disables_affected_endpoint_without_secret_or_traceback(settings, monkeypatch):
    from backend.app.db.models import LlmCredential, LlmEndpoint, LlmModel, ModelPurposeBinding
    from backend.app.db.session import create_database
    from backend.app.models.runtime_service import ModelRuntimeError, ModelRuntimeService

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", "invalid")
    database = create_database(settings); database.create_all()
    with database.session_factory() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush()
        session.add(LlmCredential(endpoint_id=endpoint.id, ciphertext=b"secret-ciphertext", nonce=b"123456789012", key_version=1, last_four="1234"))
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", enabled=True)
        session.add(model); session.flush(); session.add(ModelPurposeBinding(purpose="primary_chat", model_id=model.id)); session.commit()

        with pytest.raises(ModelRuntimeError) as exc:
            ModelRuntimeService(session).resolve("primary_chat")
        session.refresh(endpoint)
        assert exc.value.code == "MODEL_MASTER_KEY_INVALID"
        assert "secret-ciphertext" not in str(exc.value)
        assert endpoint.enabled is False
        assert endpoint.disabled_reason == "MODEL_MASTER_KEY_INVALID"


def test_setup_not_ready_when_required_binding_has_incomplete_capability_flags(client, app, monkeypatch):
    from backend.app.db.models import LlmEndpoint, LlmModel, ModelPurposeBinding

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with app.state.database.session_factory.begin() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush()
        chat = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", enabled=True,
                        capability_flags_json=json.dumps({"generation": "passed"}))
        embedding = LlmModel(endpoint_id=endpoint.id, model_name="embed", model_type="embedding", enabled=True,
                             capability_flags_json=json.dumps({"embedding": "passed"}))
        session.add_all([chat, embedding]); session.flush()
        session.add_all([ModelPurposeBinding(purpose="primary_chat", model_id=chat.id), ModelPurposeBinding(purpose="embedding", model_id=embedding.id)])
    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    status = client.get("/api/setup/status", headers={"Authorization": f"Bearer {login['access_token']}"}).json()
    assert status["ready"] is False
    assert status["configured_purposes"] == []
    assert status["missing_purposes"] == ["primary_chat"]
    assert status["missing_optional_purposes"] == ["embedding"]


def test_model_grants_are_household_scoped_and_idempotent(client, app):
    from backend.app.db.models import LlmEndpoint, LlmModel

    login = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    household = client.get("/api/households/current", headers=headers).json()
    with app.state.database.session_factory.begin() as session:
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", enabled=True)
        session.add(model); session.flush(); model_id = model.id

    first = client.put(f"/api/models/households/{household['id']}/grants/{model_id}", headers=headers)
    second = client.put(f"/api/models/households/{household['id']}/grants/{model_id}", headers=headers)
    listed = client.get(f"/api/models/households/{household['id']}/grants", headers=headers)
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert listed.json() == [{"household_id": household["id"], "model_id": model_id}]


def test_model_profile_rate_limit_returns_stable_code(settings):
    from backend.app.db.models import Account, Household, LlmEndpoint, LlmModel
    from backend.app.db.session import create_database
    from backend.app.models.quotas import QuotaExceeded, QuotaService

    database = create_database(settings); database.create_all()
    with database.session_factory() as session:
        owner = Account(username="rate-owner", password_hash="hash"); session.add(owner); session.flush()
        household = Household(name="Rate", created_by_account_id=owner.id); session.add(household)
        endpoint = LlmEndpoint(protocol="openai_compatible", display_name="Gateway", base_url="https://api.openai.com/v1", enabled=True)
        session.add(endpoint); session.flush()
        model = LlmModel(endpoint_id=endpoint.id, model_name="chat", model_type="chat", requests_per_minute=1, enabled=True)
        session.add(model); session.flush()
        quota = QuotaService(session)
        quota.configure(household.id, monthly_limit=100, requests_per_minute=10, agent_token_budget=10)
        quota.consume(household.id, model.id, 1)
        with pytest.raises(QuotaExceeded) as exc:
            quota.consume(household.id, model.id, 1)
        assert exc.value.code == "MODEL_RATE_LIMIT_EXCEEDED"
