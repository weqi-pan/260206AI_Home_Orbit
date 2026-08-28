# -*- coding: utf-8 -*-
"""Phase 6 NL intent-card generation service.

Generation uses the existing model purpose resolution (auxiliary binding) via
BudgetedModel semantics; the household's grantable tool catalog is the only
tool source offered to the model, and the model's output is parsed as strictly
untrusted JSON that the platform validates separately.
"""
import json
import re

from backend.app.models.quotas import QuotaExceeded
from backend.app.models.runtime_service import ModelRuntimeError

GENERATION_PURPOSE = "auxiliary_generation"

_SYSTEM_PROMPT = (
    "你是家庭助手平台的创作器。根据一句话描述生成一个 Agent 意图卡。"
    "只能从「可用工具」列表中选择 tools；禁止编造工具、URL、代码或导入语句。"
    "只返回一个 JSON 对象，不要输出其它文字。字段要求："
    "name(<=20字)、description(<=100字)、prompt(系统提示词)、"
    "examples(数组，元素含 user/assistant)、model_requirements(含 capabilities 数组)、"
    "skills(数组，元素含 name/instructions/knowledge_asset_ids，instructions 为 Markdown 指令)、"
    "tools(工具 key 数组)、budgets(tool_calls/tokens/wall_time_seconds/delegation_depth)、"
    "tests(数组，元素含 name/input/expected_contains)、workflow(null 或受限工作流)。"
)


class GenerationError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _extract_json(content: str) -> dict:
    text = str(content).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[\w-]*\s*", "", text)
        end = text.rfind("```")
        if end != -1:
            text = text[:end]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise GenerationError("GENERATION_RESULT_INVALID")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise GenerationError("GENERATION_RESULT_INVALID") from exc
    if not isinstance(payload, dict):
        raise GenerationError("GENERATION_RESULT_INVALID")
    return payload


def _catalog_lines(catalog: dict) -> str:
    if not catalog:
        return "（当前家庭暂无可用工具，tools 必须为空数组）"
    return "\n".join(
        f"- {item['key']}（{item['risk_level']}）：{item['display_name']} {item['description']}"
        for item in sorted(catalog.values(), key=lambda item: item["key"]))


class IntentCardGenerator:
    def __init__(self, model=None, model_resolver=None):
        self._model = model
        self._model_resolver = model_resolver

    def generate(self, description: str, catalog: dict) -> dict:
        user_prompt = (f"一句话描述：{description}\n\n可用工具（只能从中选择）：\n{_catalog_lines(catalog)}\n\n"
                       f"请生成意图卡 JSON。")
        try:
            if self._model is not None:
                model = self._model
            elif self._model_resolver is not None:
                model = self._model_resolver(GENERATION_PURPOSE)
            else:
                from backend.model.factory import get_chat_model

                model = get_chat_model()
            content = model.invoke([
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]).content
        except ModelRuntimeError as exc:
            raise GenerationError(exc.code) from exc
        except QuotaExceeded as exc:
            raise GenerationError(exc.code) from exc
        return _extract_json(content)
