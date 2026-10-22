import json
import os
from dataclasses import dataclass

from sqlalchemy import select

from backend.app.db.models import LlmCredential, LlmEndpoint, LlmModel, ModelGrant, ModelHealthCheck, ModelPurposeBinding
from backend.app.llm_runtime.credentials import CredentialCipher
from backend.app.llm_runtime.adapters import (
    LangChainChatTransport, LangChainEmbeddingTransport, ModelAdapter,
    build_langchain_chat, build_langchain_embeddings,
)
from backend.app.llm_runtime.capabilities import CapabilityTester
from backend.app.llm_runtime.runtime import ModelRuntimeSnapshot
from backend.app.llm_runtime.endpoint_security import safe_http_client, validate_endpoint_url, UnsafeEndpointUrl


class ModelRuntimeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ResolvedModelRuntime:
    snapshot: ModelRuntimeSnapshot
    protocol: str
    base_url: str
    api_key: str
    timeout_seconds: int
    max_retries: int


class ModelRuntimeService:
    def __init__(self, session):
        self.session = session

    def resolve(self, purpose: str, household_id: str | None = None, require_grant: bool = True) -> ResolvedModelRuntime:
        binding = self.session.get(ModelPurposeBinding, purpose)
        model = self.session.get(LlmModel, binding.model_id) if binding else None
        endpoint = self.session.get(LlmEndpoint, model.endpoint_id) if model else None
        credential = self.session.get(LlmCredential, endpoint.id) if endpoint else None
        if not binding or not model or not endpoint or not credential or not model.enabled or not endpoint.enabled:
            raise ModelRuntimeError("MODEL_NOT_CONFIGURED")
        try:
            api_key = CredentialCipher(os.getenv("LLM_CREDENTIAL_MASTER_KEY")).decrypt(
                endpoint.id, endpoint.protocol, credential
            )
        except Exception as exc:
            endpoint.enabled = False
            endpoint.disabled_reason = "MODEL_MASTER_KEY_INVALID"
            self.session.commit()
            raise ModelRuntimeError("MODEL_MASTER_KEY_INVALID") from exc
        expected_type = "embedding" if purpose == "embedding" else "chat"
        if model.model_type != expected_type:
            raise ModelRuntimeError("MODEL_PURPOSE_TYPE_MISMATCH")
        latest = self.session.scalar(select(ModelHealthCheck).where(ModelHealthCheck.model_id == model.id)
                                     .order_by(ModelHealthCheck.created_at.desc(), ModelHealthCheck.id.desc()))
        if not latest or latest.status != "passed":
            raise ModelRuntimeError("MODEL_CAPABILITY_NOT_VERIFIED")
        if binding.endpoint_revision != endpoint.revision or binding.model_revision != model.revision or \
                latest.endpoint_revision != endpoint.revision or latest.model_revision != model.revision:
            raise ModelRuntimeError("MODEL_CONFIGURATION_STALE")
        if require_grant:
            if not household_id:
                raise ModelRuntimeError("HOUSEHOLD_REQUIRED")
            grant = self.session.scalar(select(ModelGrant).where(ModelGrant.household_id == household_id, ModelGrant.model_id == model.id))
            if not grant:
                raise ModelRuntimeError("MODEL_NOT_GRANTED")
        profile = {
            "model_name": model.model_name,
            "model_type": model.model_type,
            "temperature": model.temperature,
            "max_tokens": model.max_tokens,
            "embedding_dimensions": model.embedding_dimensions,
            "use_responses_api": model.use_responses_api,
            "capabilities": json.loads(model.capability_flags_json or "{}"),
            "requests_per_minute": model.requests_per_minute,
            "agent_token_budget": model.agent_token_budget,
        }
        snapshot = ModelRuntimeSnapshot(purpose, model.id, endpoint.id, binding.config_revision, profile)
        return ResolvedModelRuntime(snapshot, endpoint.protocol, endpoint.base_url, api_key,
                                    endpoint.timeout_seconds, endpoint.max_retries)

    def build_chat_model(self, resolved: ResolvedModelRuntime):
        profile = resolved.snapshot.profile
        try:
            validate_endpoint_url(resolved.base_url)
            client = safe_http_client()
            return build_langchain_chat(
                resolved.protocol, api_key=resolved.api_key, model=profile["model_name"], base_url=resolved.base_url,
                timeout=resolved.timeout_seconds, max_retries=resolved.max_retries, temperature=profile["temperature"],
                max_tokens=profile["max_tokens"], use_responses_api=profile["use_responses_api"], http_client=client,
            )
        except UnsafeEndpointUrl as exc:
            raise ModelRuntimeError("ENDPOINT_URL_UNSAFE") from exc
        except Exception as exc:
            raise ModelRuntimeError("MODEL_PROVIDER_UNAVAILABLE") from exc

    def capability_tester(self, model: LlmModel) -> CapabilityTester:
        endpoint = self.session.get(LlmEndpoint, model.endpoint_id)
        credential = self.session.get(LlmCredential, endpoint.id) if endpoint else None
        if not endpoint or not credential or not endpoint.enabled:
            raise ModelRuntimeError("MODEL_NOT_CONFIGURED")
        try:
            validate_endpoint_url(endpoint.base_url)
            http_client = safe_http_client()
        except UnsafeEndpointUrl as exc:
            raise ModelRuntimeError("ENDPOINT_URL_UNSAFE") from exc
        try:
            api_key = CredentialCipher(os.getenv("LLM_CREDENTIAL_MASTER_KEY")).decrypt(
                endpoint.id, endpoint.protocol, credential
            )
        except Exception as exc:
            endpoint.enabled = False; endpoint.disabled_reason = "MODEL_MASTER_KEY_INVALID"; self.session.commit()
            raise ModelRuntimeError("MODEL_MASTER_KEY_INVALID") from exc
        if model.model_type == "embedding":
            embeddings = build_langchain_embeddings(api_key=api_key, model=model.model_name, base_url=endpoint.base_url,
                                                    dimensions=model.embedding_dimensions, http_client=http_client)
            adapter = ModelAdapter(endpoint.protocol, model.model_name,
                                   embedding_transport=LangChainEmbeddingTransport(embeddings))
        else:
            chat = build_langchain_chat(endpoint.protocol, api_key=api_key, model=model.model_name, base_url=endpoint.base_url,
                                        timeout=endpoint.timeout_seconds, max_retries=endpoint.max_retries,
                                        temperature=model.temperature, max_tokens=model.max_tokens,
                                        use_responses_api=model.use_responses_api, http_client=http_client)
            adapter = ModelAdapter(endpoint.protocol, model.model_name, chat_transport=LangChainChatTransport(chat))
        return CapabilityTester(adapter)

    def build_embeddings_for_model(self, model: LlmModel):
        endpoint = self.session.get(LlmEndpoint, model.endpoint_id)
        credential = self.session.get(LlmCredential, endpoint.id) if endpoint else None
        if not endpoint or not credential or not endpoint.enabled or model.model_type != "embedding":
            raise ModelRuntimeError("MODEL_NOT_CONFIGURED")
        try:
            validate_endpoint_url(endpoint.base_url)
            api_key = CredentialCipher(os.getenv("LLM_CREDENTIAL_MASTER_KEY")).decrypt(endpoint.id, endpoint.protocol, credential)
            return build_langchain_embeddings(api_key=api_key, model=model.model_name, base_url=endpoint.base_url,
                                              dimensions=model.embedding_dimensions, http_client=safe_http_client())
        except UnsafeEndpointUrl as exc:
            raise ModelRuntimeError("ENDPOINT_URL_UNSAFE") from exc
        except Exception as exc:
            raise ModelRuntimeError("MODEL_PROVIDER_UNAVAILABLE") from exc
