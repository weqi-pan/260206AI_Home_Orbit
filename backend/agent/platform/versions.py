import json
import re

from sqlalchemy import func, select

from backend.agent.platform.workflow import validate_workflow
from backend.app.db.models import AgentDeployment, AgentVersion, SkillVersion


class DefinitionRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class VersionImmutable(RuntimeError):
    pass


FORBIDDEN_KEY_PARTS = {"url", "uri", "http", "https", "mcp", "script", "shell", "code", "execute",
                       "import", "filesystem", "file_path", "secret", "credential", "token", "api_key"}
UNSAFE_CONTENT = re.compile(
    r"https?://|\b(?:curl|wget|powershell|cmd\.exe|bash|sh|python|javascript|node|eval|exec|os\.system|subprocess)\b|"
    r"`[^`]*(?:rm\s+-|curl|wget|python|bash|sh\s)[^`]*`",
    re.IGNORECASE,
)
SKILL_FIELDS = {"instructions", "attachments", "knowledge"}
AGENT_FIELDS = {"prompt", "model_requirements", "permissions", "budgets", "tests", "workflow",
                "child_deployments", "metadata", "tools"}
ATTACHMENT_FIELDS = {"kind", "name", "asset_id", "content_hash"}


def _unsafe_key(key):
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    parts = set(normalized.split("_"))
    return bool(parts & FORBIDDEN_KEY_PARTS) or any(normalized.endswith(f"_{item}") for item in FORBIDDEN_KEY_PARTS)


def _scan(value):
    if isinstance(value, dict):
        if any(_unsafe_key(str(key)) for key in value):
            return True
        return any(_scan(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_scan(item) for item in value)
    return isinstance(value, str) and bool(UNSAFE_CONTENT.search(value))


def _only_fields(definition, allowed, code):
    if not isinstance(definition, dict) or set(definition) - allowed or _scan(definition):
        raise DefinitionRejected(code)


def validate_skill_definition(definition: dict) -> dict:
    _only_fields(definition, SKILL_FIELDS, "SKILL_DEFINITION_UNSAFE")
    instructions = definition.get("instructions", "")
    if not isinstance(instructions, str):
        raise DefinitionRejected("SKILL_DEFINITION_INVALID")
    attachments = definition.get("attachments", [])
    if not isinstance(attachments, list):
        raise DefinitionRejected("SKILL_DEFINITION_INVALID")
    normalized_attachments = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or set(attachment) - ATTACHMENT_FIELDS:
            raise DefinitionRejected("SKILL_DEFINITION_UNSAFE")
        if attachment.get("kind") not in {"knowledge", "data"}:
            raise DefinitionRejected("SKILL_DEFINITION_UNSAFE")
        normalized_attachments.append({key: attachment[key] for key in sorted(attachment)})
    return {"instructions": instructions, "attachments": normalized_attachments,
            **({"knowledge": definition["knowledge"]} if "knowledge" in definition else {})}


def validate_agent_definition(definition: dict) -> dict:
    _only_fields(definition, AGENT_FIELDS, "AGENT_DEFINITION_UNSAFE")
    if not isinstance(definition.get("prompt", ""), str):
        raise DefinitionRejected("AGENT_DEFINITION_INVALID")
    validate_workflow(definition.get("workflow"))
    normalized = {key: definition[key] for key in sorted(definition)}
    for collection in ("permissions", "tests", "child_deployments", "tools"):
        if collection in normalized and not isinstance(normalized[collection], list):
            raise DefinitionRejected("AGENT_DEFINITION_INVALID")
    for mapping in ("model_requirements", "budgets", "metadata"):
        if mapping in normalized and not isinstance(normalized[mapping], dict):
            raise DefinitionRejected("AGENT_DEFINITION_INVALID")
    return normalized


def normalize_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class VersionView:
    def __init__(self, row):
        self.row = row
        self.id = row.id
        self.definition = json.loads(row.definition_json)


class AgentVersionService:
    def __init__(self, session, household_id):
        self.session = session
        self.household_id = household_id

    def publish(self, agent_id, definition):
        safe = validate_agent_definition(definition)
        number = self.session.scalar(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent_id)) or 0
        row = AgentVersion(household_id=self.household_id, agent_id=agent_id, version=number + 1,
                           definition_json=normalize_json(safe), published_at=func.now())
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return VersionView(row)

    def replace(self, version_id, definition):
        row = self.session.get(AgentVersion, version_id)
        if row and row.published_at:
            raise VersionImmutable(version_id)
        row.definition_json = normalize_json(validate_agent_definition(definition)); self.session.commit()
        return VersionView(row)

    def get(self, version_id):
        return VersionView(self.session.get(AgentVersion, version_id))

    def deploy(self, version_id):
        row = AgentDeployment(household_id=self.household_id, agent_version_id=version_id)
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return row

    def switch(self, deployment_id, version_id):
        deployment = self.session.get(AgentDeployment, deployment_id)
        version = self.session.get(AgentVersion, version_id)
        if not version or version.household_id != self.household_id or not deployment or deployment.household_id != self.household_id:
            raise DefinitionRejected("HOUSEHOLD_MISMATCH")
        deployment.agent_version_id = version_id; deployment.revision += 1; self.session.commit()
        return deployment


class SkillVersionService:
    def __init__(self, session, household_id):
        self.session = session
        self.household_id = household_id

    def publish(self, skill_id, definition):
        safe = validate_skill_definition(definition)
        number = self.session.scalar(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill_id)) or 0
        row = SkillVersion(household_id=self.household_id, skill_id=skill_id, version=number + 1,
                           definition_json=normalize_json(safe), published_at=func.now())
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return VersionView(row)

    def replace(self, version_id, definition):
        row = self.session.get(SkillVersion, version_id)
        if row and row.published_at:
            raise VersionImmutable(version_id)
        row.definition_json = normalize_json(validate_skill_definition(definition)); self.session.commit()
        return VersionView(row)
