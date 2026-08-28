import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    r"\bimport\s+[a-z_.]+|\bfrom\s+[a-z_.]+\s+import\b|"
    r"`[^`]*(?:rm\s+-|curl|wget|python|bash|sh\s)[^`]*`",
    re.IGNORECASE,
)
SKILL_FIELDS = {"instructions", "attachments", "knowledge"}
AGENT_FIELDS = {"prompt", "model_requirements", "permissions", "budgets", "tests", "workflow",
                "child_deployments", "metadata", "tools"}
ATTACHMENT_FIELDS = {"kind", "name", "asset_id", "content_hash"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SkillAttachmentSchema(StrictModel):
    kind: Literal["knowledge", "data"]
    name: str = Field(min_length=1, max_length=200)
    asset_id: str = Field(min_length=1, max_length=100)
    content_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class SkillSchema(StrictModel):
    instructions: str = Field(min_length=1, max_length=50000)
    knowledge: list[str] = Field(default_factory=list)
    attachments: list[SkillAttachmentSchema] = Field(default_factory=list)


class ModelRequirementsSchema(StrictModel):
    capabilities: list[str] = Field(default_factory=list)


class PermissionSchema(StrictModel):
    tool_key: str = Field(min_length=1, max_length=100)
    required: bool = True


class BudgetSchema(StrictModel):
    tool_calls: int = Field(default=16, ge=1, le=64)
    tokens: int = Field(default=32000, ge=1, le=100000)
    wall_time_seconds: int = Field(default=120, ge=1, le=300)
    delegation_depth: int = Field(default=4, ge=0, le=16)


class AgentTestSchema(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=5000)
    expected_contains: str = Field(min_length=1, max_length=1000)


class MetadataSchema(StrictModel):
    description: str = Field(default="", max_length=500)


class AgentSchema(StrictModel):
    prompt: str = Field(min_length=1, max_length=50000)
    model_requirements: ModelRequirementsSchema = Field(default_factory=ModelRequirementsSchema)
    permissions: list[PermissionSchema] = Field(default_factory=list)
    budgets: BudgetSchema = Field(default_factory=BudgetSchema)
    tests: list[AgentTestSchema] = Field(default_factory=list)
    workflow: dict | None = None
    tools: list[str] = Field(default_factory=list)
    child_deployments: list[str] = Field(default_factory=list)
    metadata: MetadataSchema = Field(default_factory=MetadataSchema)


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
    if any(isinstance(item, dict) and item.get("kind") not in {"knowledge", "data"}
           for item in definition.get("attachments", []) if isinstance(definition.get("attachments"), list)):
        raise DefinitionRejected("SKILL_DEFINITION_UNSAFE")
    try:
        return SkillSchema.model_validate(definition).model_dump(mode="json")
    except ValidationError as exc:
        raise DefinitionRejected("SKILL_DEFINITION_INVALID") from exc


def validate_agent_definition(definition: dict) -> dict:
    _only_fields(definition, AGENT_FIELDS, "AGENT_DEFINITION_UNSAFE")
    try:
        normalized = AgentSchema.model_validate(definition).model_dump(mode="json")
        validate_workflow(normalized.get("workflow"))
        return normalized
    except (ValidationError, ValueError) as exc:
        raise DefinitionRejected("AGENT_DEFINITION_INVALID") from exc


def normalize_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class VersionView:
    def __init__(self, row):
        self.row = row
        self.id = row.id
        self.version = row.version
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
