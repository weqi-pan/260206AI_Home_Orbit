import hashlib
import json

from backend.agent.platform.authority import ExecutionAuthority
from backend.agent.platform.context import _freeze
from backend.app.db.models import AgentDeployment, AgentToolBinding, AgentVersion, SkillVersion
from sqlalchemy import select
from backend.app.db.models import AgentSkillBinding


def _hash_json(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CompiledSnapshotService:
    def __init__(self, session):
        self.session = session

    def capture(self, deployment_id, installation_id, context):
        deployment, installation = ExecutionAuthority(self.session).installation(
            deployment_id, context.household_id, context.agent_version_id)
        if installation.id != installation_id:
            raise RuntimeError("INSTALLATION_REQUIRED")
        version = self.session.get(AgentVersion, deployment.agent_version_id)
        skills = self.session.scalars(select(SkillVersion).join(
            AgentSkillBinding, AgentSkillBinding.skill_version_id == SkillVersion.id).where(
                AgentSkillBinding.agent_version_id == version.id,
                SkillVersion.household_id == context.household_id)).all()
        bound_tool_ids = set(self.session.scalars(select(AgentToolBinding.tool_id).where(
            AgentToolBinding.agent_version_id == version.id, AgentToolBinding.household_id == context.household_id)).all())
        tools = ExecutionAuthority(self.session).tools(deployment.id, context.household_id, version.id, bound_tool_ids)
        snapshot = {
            "deployment": {"id": deployment.id, "revision": deployment.revision},
            "agent_version": {"id": version.id, "version": version.version, "definition": json.loads(version.definition_json),
                              "config_hash": _hash_json(version.definition_json)},
            "skills": [{"id": row.id, "version": row.version, "definition": json.loads(row.definition_json),
                        "config_hash": _hash_json(row.definition_json)} for row in skills],
            "tools": [{"key": row.key, "version": row.version, "risk_level": row.risk_level} for row in tools],
            "model": {"id": context.model_id, "revision": context.model_revision},
            "installation": {"id": installation.id, "agent_version_id": installation.agent_version_id},
            "grants": {row.key: {"version": row.version, "risk_level": row.risk_level} for row in tools},
            "consent": dict(context.consent),
        }
        return _freeze(snapshot)
