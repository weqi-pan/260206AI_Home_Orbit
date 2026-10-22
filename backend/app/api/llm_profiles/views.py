import json

from backend.app.db.models import LlmCredential, LlmEndpoint, LlmModel


def endpoint_view(item: LlmEndpoint, credential: LlmCredential | None):
    return {
        "id": item.id, "protocol": item.protocol, "display_name": item.display_name, "base_url": item.base_url,
        "timeout_seconds": item.timeout_seconds, "max_retries": item.max_retries, "enabled": item.enabled,
        "revision": item.revision, "disabled_reason": item.disabled_reason,
        "has_api_key": credential is not None, "masked_api_key": f"****{credential.last_four}" if credential else None,
    }


def model_view(item: LlmModel):
    return {
        "id": item.id, "endpoint_id": item.endpoint_id, "model_name": item.model_name, "model_type": item.model_type,
        "temperature": item.temperature, "max_tokens": item.max_tokens, "embedding_dimensions": item.embedding_dimensions,
        "use_responses_api": item.use_responses_api, "capability_flags": json.loads(item.capability_flags_json or "{}"),
        "requests_per_minute": item.requests_per_minute, "agent_token_budget": item.agent_token_budget, "enabled": item.enabled,
    }
