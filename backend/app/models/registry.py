import json

from sqlalchemy import select

from backend.app.db.models import LlmEndpoint, LlmModel, ModelHealthCheck, ModelPurposeBinding


CHAT_CAPABILITIES = {"generation", "streaming", "cancellation", "tool_calling", "structured_output"}
PURPOSE_TYPES = {
    "household_orchestrator": "chat", "primary_chat": "chat", "auxiliary_generation": "chat",
    "review_model": "chat", "embedding": "embedding",
}


class RegistryValidationError(ValueError):
    def __init__(self, code: str): self.code = code; super().__init__(code)


class BindingRejected(RegistryValidationError):
    pass


class ModelRegistry:
    def __init__(self, session): self.session = session

    def create_model(self, endpoint_id: str, model_name: str, model_type: str, **values):
        endpoint = self.session.get(LlmEndpoint, endpoint_id)
        if not endpoint:
            raise RegistryValidationError("ENDPOINT_NOT_FOUND")
        if model_type not in {"chat", "embedding"}:
            raise RegistryValidationError("MODEL_TYPE_INVALID")
        if endpoint.protocol == "anthropic" and model_type == "embedding":
            raise RegistryValidationError("ANTHROPIC_CHAT_ONLY")
        model = LlmModel(endpoint_id=endpoint_id, model_name=model_name, model_type=model_type, **values)
        self.session.add(model); self.session.commit(); self.session.refresh(model)
        return model

    def record_capability_result(self, model_id: str, capabilities: dict[str, str], error_code=None, evidence=None):
        passed = all(value == "passed" for value in capabilities.values())
        check = ModelHealthCheck(model_id=model_id, status="passed" if passed else "failed",
                                 capabilities_json=json.dumps(capabilities), evidence_json=json.dumps(evidence or {}), error_code=error_code)
        self.session.add(check)
        model = self.session.get(LlmModel, model_id)
        model.capability_flags_json = json.dumps(capabilities)
        self.session.commit()
        return check

    def bind(self, purpose: str, model_id: str):
        expected_type = PURPOSE_TYPES.get(purpose)
        if not expected_type:
            raise BindingRejected("PURPOSE_INVALID")
        model = self.session.get(LlmModel, model_id)
        endpoint = self.session.get(LlmEndpoint, model.endpoint_id) if model else None
        capabilities = json.loads(model.capability_flags_json or "{}") if model else {}
        required = CHAT_CAPABILITIES if expected_type == "chat" else {"embedding"}
        if not model or not endpoint or not model.enabled or not endpoint.enabled or model.model_type != expected_type:
            raise BindingRejected("MODEL_NOT_ENABLED")
        if any(capabilities.get(name) != "passed" for name in required):
            raise BindingRejected("MODEL_CAPABILITY_NOT_VERIFIED")
        binding = self.session.get(ModelPurposeBinding, purpose)
        if not binding:
            binding = ModelPurposeBinding(purpose=purpose, model_id=model_id, config_revision=1)
            self.session.add(binding)
        else:
            binding.model_id = model_id
            binding.config_revision += 1
        endpoint.revision += 1
        self.session.commit(); self.session.refresh(binding)
        return binding
