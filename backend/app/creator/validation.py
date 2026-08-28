"""Phase 6 creator draft validation: the platform-side trust boundary.

The LLM proposes; the platform validates. Validation enforces the Phase 3
strict Agent/Skill schemas (forbidden keys/content/code/URL/import/shell/
HTTP/MCP/secret rejection), the household tool permission ceiling (exact
registry intersection), workflow schema/limits, budget sanity and
prompt-injection scanning of generated prompts/instructions/examples before
anything is persisted as validated.
"""
import re

from sqlalchemy import select

from backend.agent.platform.catalog import BuiltinToolRegistry
from backend.agent.platform.versions import (AGENT_FIELDS, UNSAFE_CONTENT, validate_agent_definition,
                                             validate_skill_definition)
from backend.agent.platform.workflow import validate_workflow
from backend.app.creator.schemas import IntentCardSchema, parse_intent_card
from backend.app.db.models import CreatorKnowledgeAsset, ToolDefinition, ToolPermission


class DraftValidationRejected(ValueError):
    """Carries every stable rejection code plus a human-readable detail."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(";".join(error["code"] for error in errors))


INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"忽略[之以]?.{0,4}(以上|之前|前面|所有|先前).{0,6}(指示|指令|说明|规则|提示|提示词|约束)", "override_instructions"),
    (r"ignore\s+(all\s+|any\s+|previous\s+|prior\s+|above\s+)?(instructions?|rules?|prompts?|constraints?)",
     "override_instructions"),
    (r"disregard\s+(all\s+|any\s+|previous\s+|prior\s+)?(instructions?|rules?)", "override_instructions"),
    (r"(泄露|输出|显示|打印|告诉我).{0,16}(系统提示|提示词|系统指令|密钥|秘钥|密码|api\s?key|secret)", "exfiltrate_secrets"),
    (r"(reveal|print|show|output|leak)\s.{0,32}(system\s+prompt|api\s*key|secret|password|credential)",
     "exfiltrate_secrets"),
)

BUDGET_BOUNDS = {"tool_calls": (1, 64), "tokens": (1, 100000), "wall_time_seconds": (1, 300),
                 "delegation_depth": (0, 16)}


def scan_injection(text: str) -> str | None:
    for pattern, label in INJECTION_PATTERNS:
        if text and re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def _card_text_fields(card: IntentCardSchema):
    yield "name", card.name
    yield "description", card.description
    for index, example in enumerate(card.examples):
        yield f"examples[{index}].user", example.user
        yield f"examples[{index}].assistant", example.assistant
    for index, case in enumerate(card.tests):
        yield f"tests[{index}].input", case.input


class DraftValidator:
    def __init__(self, session, household_id, registry=None):
        self.session = session
        self.household_id = household_id
        self.registry = registry or BuiltinToolRegistry.default()

    # ------------------------------------------------------------------ ceiling
    def ceiling_tools(self) -> dict[str, dict]:
        """Tools this household can lawfully reference: hardcoded registry ∩
        enabled ToolDefinition ∩ household ToolPermission with exact version/risk."""
        result = {}
        rows = self.session.execute(select(ToolDefinition, ToolPermission).join(
            ToolPermission, ToolPermission.tool_id == ToolDefinition.id).where(
            ToolPermission.household_id == self.household_id)).all()
        for tool, permission in rows:
            try:
                metadata = self.registry.resolve(tool.key).metadata
            except KeyError:
                continue
            if not tool.enabled or metadata.version != tool.version or metadata.risk_level != tool.risk_level:
                continue
            result[tool.key] = {"tool_id": tool.id, "key": tool.key, "version": tool.version,
                                "risk_level": tool.risk_level, "display_name": tool.display_name,
                                "description": tool.description}
        return result

    def _classify_tool(self, key: str) -> dict | None:
        """Returns grantable tool info or None; rejection codes are collected by the caller."""
        try:
            metadata = self.registry.resolve(key).metadata
        except KeyError:
            return None
        row = self.session.scalar(select(ToolDefinition).where(ToolDefinition.key == key))
        if row is None:
            return None
        if not row.enabled:
            return {"__code__": "TOOL_NOT_ENABLED"}
        if metadata.version != row.version or metadata.risk_level != row.risk_level:
            return {"__code__": "TOOL_CATALOG_MISMATCH"}
        permission = self.session.scalar(select(ToolPermission).where(
            ToolPermission.household_id == self.household_id, ToolPermission.tool_id == row.id))
        if permission is None or permission.tool_version != row.version:
            return {"__code__": "TOOL_NOT_GRANTED"}
        return {"tool_id": row.id, "key": row.key, "version": row.version, "risk_level": row.risk_level,
                "display_name": row.display_name, "description": row.description}

    # --------------------------------------------------------------- validation
    def validate(self, card_payload: dict, draft_id: str | None = None) -> dict:
        errors: list[dict] = []

        try:
            card = parse_intent_card(card_payload)
        except ValueError as exc:
            raise DraftValidationRejected(
                [{"code": "INTENT_CARD_INVALID", "path": "", "detail": str(exc)[:200]}]) from exc

        for path, text in _card_text_fields(card):
            if UNSAFE_CONTENT.search(text or ""):
                errors.append({"code": "INTENT_CARD_UNSAFE", "path": path, "detail": "forbidden content"})
            label = scan_injection(text or "")
            if label:
                errors.append({"code": "PROMPT_INJECTION_DETECTED", "path": path, "detail": label})

        budgets = card.budgets.model_dump()
        for field, (low, high) in BUDGET_BOUNDS.items():
            value = budgets.get(field)
            if not isinstance(value, int) or not low <= value <= high:
                errors.append({"code": "BUDGET_INVALID", "path": f"budgets.{field}",
                               "detail": f"must be {low}..{high}"})

        resolved_tools: dict[str, dict] = {}
        for key in card.tools:
            info = self._classify_tool(key)
            if info is None:
                errors.append({"code": "TOOL_NOT_IN_CATALOG", "path": f"tools.{key}",
                               "detail": "unknown or unsynced tool key"})
            elif "__code__" in info:
                errors.append({"code": info["__code__"], "path": f"tools.{key}", "detail": ""})
            else:
                resolved_tools[key] = info

        assets = self._referenced_assets(card, draft_id, errors)

        for index, skill in enumerate(card.skills):
            label = scan_injection(skill.instructions)
            if label:
                errors.append({"code": "PROMPT_INJECTION_DETECTED",
                               "path": f"skills[{index}].instructions", "detail": label})

        if card.workflow:
            try:
                validate_workflow(card.workflow)
            except ValueError as exc:
                errors.append({"code": getattr(exc, "code", "WORKFLOW_INVALID"), "path": "workflow",
                               "detail": ""})

        for path, text in (("prompt", card.prompt),):
            label = scan_injection(text)
            if label:
                errors.append({"code": "PROMPT_INJECTION_DETECTED", "path": path, "detail": label})

        agent_definition = {
            "prompt": card.prompt,
            "model_requirements": {"capabilities": card.model_requirements.capabilities},
            "permissions": [{"tool_key": key, "required": True} for key in card.tools],
            "budgets": budgets,
            "tests": [case.model_dump() for case in card.tests],
            "tools": list(card.tools),
            "metadata": {"description": card.description[:500]},
        }
        if card.workflow:
            agent_definition["workflow"] = card.workflow

        skill_definitions = []
        for skill in card.skills:
            attachments = []
            knowledge_ids = []
            for asset_id in skill.knowledge_asset_ids:
                asset = assets.get(asset_id)
                if asset is None:
                    continue
                knowledge_ids.append(asset.id)
                attachments.append({"kind": "knowledge", "name": asset.filename, "asset_id": asset.id,
                                    "content_hash": asset.content_hash})
            definition = {"instructions": skill.instructions, "knowledge": knowledge_ids,
                          "attachments": attachments}
            skill_definitions.append({"name": skill.name, "definition": definition})

        if errors:
            raise DraftValidationRejected(errors)

        try:
            agent_definition = validate_agent_definition(agent_definition)
        except ValueError as exc:
            raise DraftValidationRejected(
                [{"code": getattr(exc, "code", "AGENT_DEFINITION_INVALID"), "path": "", "detail": ""}]) from exc
        finalized_skills = []
        for skill in skill_definitions:
            try:
                finalized_skills.append({"name": skill["name"],
                                         "definition": validate_skill_definition(skill["definition"])})
            except ValueError as exc:
                raise DraftValidationRejected(
                    [{"code": getattr(exc, "code", "SKILL_DEFINITION_INVALID"), "path": "", "detail": ""}]) from exc

        return {"card": card.model_dump(mode="json"), "agent_definition": agent_definition,
                "skills": finalized_skills, "tools": resolved_tools}

    def _referenced_assets(self, card, draft_id, errors) -> dict[str, CreatorKnowledgeAsset]:
        assets: dict[str, CreatorKnowledgeAsset] = {}
        for index, skill in enumerate(card.skills):
            for asset_id in skill.knowledge_asset_ids:
                asset = self.session.get(CreatorKnowledgeAsset, asset_id)
                if (asset is None or asset.household_id != self.household_id or asset.status != "active"
                        or asset.scan_status != "passed" or (draft_id is not None and asset.draft_id != draft_id)):
                    errors.append({"code": "KNOWLEDGE_ASSET_INVALID",
                                   "path": f"skills[{index}].knowledge_asset_ids", "detail": asset_id})
                    continue
                assets[asset_id] = asset
        return assets
