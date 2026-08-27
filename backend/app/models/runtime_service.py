import json
import os
from dataclasses import dataclass

from backend.app.db.models import LlmCredential, LlmEndpoint, LlmModel, ModelPurposeBinding
from backend.app.models.credentials import CredentialCipher
from backend.app.models.adapters import (
    LangChainChatTransport, LangChainEmbeddingTransport, ModelAdapter,
    build_langchain_chat, build_langchain_embeddings,
)
from backend.app.models.capabilities import CapabilityTester
from backend.app.models.runtime import ModelRuntimeSnapshot


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

    def resolve(self, purpose: str) -> ResolvedModelRuntime:
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

    def capability_tester(self, model: LlmModel) -> CapabilityTester:
        endpoint = self.session.get(LlmEndpoint, model.endpoint_id)
        credential = self.session.get(LlmCredential, endpoint.id) if endpoint else None
        if not endpoint or not credential or not endpoint.enabled:
            raise ModelRuntimeError("MODEL_NOT_CONFIGURED")
        try:
            api_key = CredentialCipher(os.getenv("LLM_CREDENTIAL_MASTER_KEY")).decrypt(
                endpoint.id, endpoint.protocol, credential
            )
        except Exception as exc:
            endpoint.enabled = False; endpoint.disabled_reason = "MODEL_MASTER_KEY_INVALID"; self.session.commit()
            raise ModelRuntimeError("MODEL_MASTER_KEY_INVALID") from exc
        if model.model_type == "embedding":
            embeddings = build_langchain_embeddings(api_key=api_key, model=model.model_name, base_url=endpoint.base_url,
                                                    dimensions=model.embedding_dimensions)
            adapter = ModelAdapter(endpoint.protocol, model.model_name,
                                   embedding_transport=LangChainEmbeddingTransport(embeddings))
        else:
            chat = build_langchain_chat(endpoint.protocol, api_key=api_key, model=model.model_name, base_url=endpoint.base_url,
                                        timeout=endpoint.timeout_seconds, max_retries=endpoint.max_retries,
                                        temperature=model.temperature, max_tokens=model.max_tokens,
                                        use_responses_api=model.use_responses_api)
            adapter = ModelAdapter(endpoint.protocol, model.model_name, chat_transport=LangChainChatTransport(chat))
        return CapabilityTester(adapter)
