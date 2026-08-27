import json

from sqlalchemy import func, select

from backend.agent.platform.workflow import validate_workflow
from backend.app.db.models import AgentDeployment, AgentVersion, SkillVersion


class DefinitionRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class VersionImmutable(RuntimeError):
    pass


FORBIDDEN_KEYS = {"url", "http", "mcp", "helper_script", "script", "code", "execute", "import", "secret_reference"}


def _contains_unsafe(value, key=""):
    if key.lower() in FORBIDDEN_KEYS:
        return True
    if isinstance(value, dict):
        return any(_contains_unsafe(item, str(item_key)) for item_key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_unsafe(item) for item in value)
    return False


def validate_skill_definition(definition: dict) -> dict:
    if _contains_unsafe(definition):
        raise DefinitionRejected("SKILL_DEFINITION_UNSAFE")
    for attachment in definition.get("attachments", []):
        if attachment.get("kind") not in {"knowledge", "data"}:
            raise DefinitionRejected("SKILL_DEFINITION_UNSAFE")
    return definition


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
        validate_workflow(definition.get("workflow"))
        number = self.session.scalar(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent_id)) or 0
        row = AgentVersion(household_id=self.household_id, agent_id=agent_id, version=number + 1,
                           definition_json=normalize_json(definition), published_at=func.now())
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return VersionView(row)

    def replace(self, version_id, definition):
        row = self.session.get(AgentVersion, version_id)
        if row and row.published_at:
            raise VersionImmutable(version_id)
        row.definition_json = normalize_json(definition); self.session.commit()
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
        if not version or version.household_id != self.household_id or deployment.household_id != self.household_id:
            raise DefinitionRejected("HOUSEHOLD_MISMATCH")
        deployment.agent_version_id = version_id
        deployment.revision += 1
        self.session.commit()
        return deployment


class SkillVersionService:
    def __init__(self, session, household_id):
        self.session = session
        self.household_id = household_id

    def publish(self, skill_id, definition):
        validate_skill_definition(definition)
        number = self.session.scalar(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill_id)) or 0
        row = SkillVersion(household_id=self.household_id, skill_id=skill_id, version=number + 1,
                           definition_json=normalize_json(definition), published_at=func.now())
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return VersionView(row)

    def replace(self, version_id, definition):
        row = self.session.get(SkillVersion, version_id)
        if row and row.published_at:
            raise VersionImmutable(version_id)
        validate_skill_definition(definition)
        row.definition_json = normalize_json(definition); self.session.commit()
        return VersionView(row)
