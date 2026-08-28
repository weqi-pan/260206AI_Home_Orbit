"""Phase 6 creator domain: intent-card schemas.

The intent card is the visual editing surface for a creator draft; the platform
converts it into strict declarative Agent/Skill definitions during validation.
LLM output is untrusted: every field is bounded and extra fields are forbidden.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CardExampleSchema(StrictModel):
    user: str = Field(min_length=1, max_length=2000)
    assistant: str = Field(max_length=4000, default="")


class CardSkillSchema(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=50000)
    knowledge_asset_ids: list[str] = Field(default_factory=list, max_length=16)


class CardTestSchema(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=5000)
    expected_contains: str = Field(min_length=1, max_length=1000)


class CardBudgetsSchema(StrictModel):
    """Bounds are enforced by DraftValidator with the stable BUDGET_INVALID code."""

    tool_calls: int = 16
    tokens: int = 32000
    wall_time_seconds: int = 120
    delegation_depth: int = 4


class CardModelRequirementsSchema(StrictModel):
    capabilities: list[str] = Field(default_factory=list, max_length=8)


class IntentCardSchema(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    prompt: str = Field(min_length=1, max_length=20000)
    examples: list[CardExampleSchema] = Field(default_factory=list, max_length=8)
    model_requirements: CardModelRequirementsSchema = Field(default_factory=CardModelRequirementsSchema)
    skills: list[CardSkillSchema] = Field(default_factory=list, max_length=8)
    tools: list[str] = Field(default_factory=list, max_length=16)
    budgets: CardBudgetsSchema = Field(default_factory=CardBudgetsSchema)
    tests: list[CardTestSchema] = Field(default_factory=list, max_length=16)
    workflow: dict | None = None


class DraftCreateRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)


class DraftUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    intent_card: dict | None = None


class GenerateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class PublishRequest(BaseModel):
    granted_tool_keys: list[str] = Field(default_factory=list, max_length=32)


class TrialRequest(BaseModel):
    granted_tool_keys: list[str] = Field(default_factory=list, max_length=32)


class KnowledgeUploadRequest(BaseModel):
    skill_key: str = Field(default="", max_length=100)


def parse_intent_card(payload: dict) -> IntentCardSchema:
    try:
        return IntentCardSchema.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"INTENT_CARD_INVALID: {exc.errors()[0].get('loc', '')}") from exc


__all__ = ["IntentCardSchema", "DraftCreateRequest", "DraftUpdateRequest", "GenerateRequest",
           "PublishRequest", "TrialRequest", "KnowledgeUploadRequest", "parse_intent_card",
           "Literal"]
