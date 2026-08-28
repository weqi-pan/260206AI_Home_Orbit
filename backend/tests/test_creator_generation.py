# -*- coding: utf-8 -*-
"""Phase 6 NL intent-card generation: model purpose resolution, household
catalog constraint, untrusted-output parsing and graceful model-unready codes."""
import json

import pytest
from sqlalchemy import select

from backend.tests.test_celery_tasks import _home


class FakeContent:
    def __init__(self, content):
        self.content = content


class FakeModel:
    def __init__(self, content):
        self._content = content
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages)
        return FakeContent(self._content)


GOOD_CARD = json.dumps({
    "name": "广东菜大厨",
    "description": "提供几道广东菜做法",
    "prompt": "你是家庭粤菜大厨，回答家常粤菜做法。",
    "examples": [{"user": "推荐一道粤菜", "assistant": "白切鸡。"}],
    "model_requirements": {"capabilities": ["chat"]},
    "skills": [{"name": "粤菜菜谱", "instructions": "根据菜谱知识回答做法。", "knowledge_asset_ids": []}],
    "tools": ["household.recipe.scale"],
    "budgets": {"tool_calls": 8, "tokens": 8000, "wall_time_seconds": 60, "delegation_depth": 1},
    "tests": [{"name": "推荐", "input": "推荐一道粤菜", "expected_contains": "菜"}],
    "workflow": None,
}, ensure_ascii=False)


def _generator(app, model=None):
    from backend.app.creator.generation import IntentCardGenerator

    _account_id, household_id = _home(app)
    with app.state.database.session_factory() as db:
        from backend.app.creator.validation import DraftValidator

        validator = DraftValidator(db, household_id)
        return (IntentCardGenerator(model=model) if model else IntentCardGenerator()), validator, household_id


def test_generation_uses_auxiliary_purpose_and_catalog_and_returns_card(client, app):
    from backend.app.models.execution import purpose_model_resolver

    purposes = []

    class RecordingResolver:
        def __call__(self, purpose):
            purposes.append(purpose)
            return FakeModel(GOOD_CARD)

    generator, validator, household_id = _generator(app)
    generator = type(generator)(model_resolver=RecordingResolver())
    card = generator.generate("提供几道广东菜做法", catalog=validator.ceiling_tools())
    assert purposes == ["auxiliary_generation"]
    assert card["name"] == "广东菜大厨"
    validator.validate(card)  # 生成结果必须能通过平台校验


def test_generation_prompt_only_offers_household_available_tools(client, app):
    generator, validator, household_id = _generator(app)
    model = FakeModel(GOOD_CARD)
    generator = type(generator)(model=model)
    generator.generate("提供几道广东菜做法", catalog=validator.ceiling_tools())
    prompt_text = json.dumps([str(message) for message in model.prompts], ensure_ascii=False)
    assert "household.recipe.scale" in prompt_text
    assert "household.shopping.write" not in prompt_text  # 未授予家庭，不进入候选


def test_generation_output_is_untrusted_and_unparsable_output_rejected(client, app):
    from backend.app.creator.generation import GenerationError

    generator, _validator, _household_id = _generator(app, model=FakeModel("我觉得你应该……"))
    with pytest.raises(GenerationError) as exc:
        generator.generate("提供几道广东菜做法", catalog={})
    assert exc.value.code == "GENERATION_RESULT_INVALID"

    generator2, _validator, _household_id = _generator(app, model=FakeModel("```json\n" + GOOD_CARD + "\n```"))
    card = generator2.generate("提供几道广东菜做法", catalog={})
    assert card["name"] == "广东菜大厨"


def test_generation_model_unready_maps_to_stable_codes(app, monkeypatch):
    from backend.app.creator.generation import GenerationError, IntentCardGenerator
    from backend.app.models.quotas import QuotaExceeded
    from backend.app.models.runtime_service import ModelRuntimeError

    class BrokenResolver:
        def __call__(self, purpose):
            raise ModelRuntimeError("MODEL_NOT_CONFIGURED")

    with pytest.raises(GenerationError) as exc:
        IntentCardGenerator(model_resolver=BrokenResolver()).generate("描述", catalog={})
    assert exc.value.code == "MODEL_NOT_CONFIGURED"

    class QuotaResolver:
        def __call__(self, purpose):
            raise QuotaExceeded("HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED")

    with pytest.raises(GenerationError) as exc:
        IntentCardGenerator(model_resolver=QuotaResolver()).generate("描述", catalog={})
    assert exc.value.code == "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"
