from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


Protocol = Literal["openai_compatible", "anthropic"]
ModelType = Literal["chat", "embedding"]
Purpose = Literal["household_orchestrator", "primary_chat", "auxiliary_generation", "review_model", "embedding"]


class EndpointCreate(BaseModel):
    protocol: Protocol
    display_name: str = Field(min_length=1, max_length=100)
    base_url: HttpUrl
    api_key: str | None = Field(default=None, min_length=1)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_retries: int = Field(default=2, ge=0, le=10)
    enabled: bool = False


class EndpointUpdate(BaseModel):
    protocol: Protocol | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: HttpUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    clear_api_key: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    enabled: bool | None = None

    @model_validator(mode="after")
    def key_action_is_unambiguous(self):
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("api_key and clear_api_key cannot be combined")
        return self


class EndpointView(BaseModel):
    id: str
    protocol: Protocol
    display_name: str
    base_url: str
    timeout_seconds: int
    max_retries: int
    enabled: bool
    revision: int
    disabled_reason: str | None
    has_api_key: bool
    masked_api_key: str | None


class ModelCreate(BaseModel):
    endpoint_id: str
    model_name: str = Field(min_length=1, max_length=200)
    model_type: ModelType
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    embedding_dimensions: int | None = Field(default=None, ge=1)
    use_responses_api: bool = False
    requests_per_minute: int = Field(default=60, ge=1)
    agent_token_budget: int | None = Field(default=None, ge=1)
    enabled: bool = False


class ModelUpdate(BaseModel):
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    embedding_dimensions: int | None = Field(default=None, ge=1)
    use_responses_api: bool | None = None
    requests_per_minute: int | None = Field(default=None, ge=1)
    agent_token_budget: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class ModelView(BaseModel):
    id: str
    endpoint_id: str
    model_name: str
    model_type: ModelType
    temperature: float
    max_tokens: int | None
    embedding_dimensions: int | None
    use_responses_api: bool
    capability_flags: dict[str, str]
    requests_per_minute: int
    agent_token_budget: int | None
    enabled: bool


class CapabilityResult(BaseModel):
    passed: bool
    capabilities: dict[str, str]
    error_code: str | None
    dimensions: int | None = None


class PurposeBindingRequest(BaseModel):
    purpose: Purpose
    model_id: str


class QuotaRequest(BaseModel):
    monthly_limit: int = Field(ge=0)
    requests_per_minute: int = Field(ge=1)
    agent_token_budget: int | None = Field(default=None, ge=1)
