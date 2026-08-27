import hashlib
import json

from sqlalchemy import select

from backend.agent.platform.authority import ExecutionAuthority
from backend.agent.platform.context import _freeze
from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentSkillBinding, AgentToolBinding,
                                   AgentVersion, SkillVersion)


def _hash_json(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CompiledSnapshotService:
    def __init__(self, session):
        self.session = session

    def _capture_deployment(self, deployment_id, household_id, agent_version_id, seen):
        if deployment_id in seen:
            raise RuntimeError("CHILD_DEPLOYMENT_CYCLE")
        seen = {*seen, deployment_id}
        deployment, installation = ExecutionAuthority(self.session).installation(
            deployment_id, household_id, agent_version_id)
        version = self.session.get(AgentVersion, deployment.agent_version_id)
        agent = self.session.get(AgentDefinition, version.agent_id)
        definition = json.loads(version.definition_json)
        skills = self.session.scalars(select(SkillVersion).join(
            AgentSkillBinding, AgentSkillBinding.skill_version_id == SkillVersion.id).where(
                AgentSkillBinding.agent_version_id == version.id,
                SkillVersion.household_id == household_id)).all()
        bound_tool_ids = set(self.session.scalars(select(AgentToolBinding.tool_id).where(
            AgentToolBinding.agent_version_id == version.id,
            AgentToolBinding.household_id == household_id)).all())
        tools = ExecutionAuthority(self.session).tools(deployment.id, household_id, version.id, bound_tool_ids)
        children = []
        for child_id in definition.get("child_deployments", []):
            child_deployment = self.session.get(AgentDeployment, child_id)
            if not child_deployment or child_deployment.household_id != household_id:
                raise RuntimeError("CHILD_DEPLOYMENT_INVALID")
            children.append(self._capture_deployment(child_id, household_id, child_deployment.agent_version_id, seen))
        return {
            "deployment": {"id": deployment.id, "revision": deployment.revision},
            "agent": {"id": agent.id, "name": agent.name},
            "agent_version": {"id": version.id, "version": version.version, "definition": definition,
                              "config_hash": _hash_json(version.definition_json)},
            "skills": [{"id": row.id, "skill_id": row.skill_id, "version": row.version,
                        "definition": json.loads(row.definition_json), "config_hash": _hash_json(row.definition_json)}
                       for row in skills],
            "tools": [{"tool_id": row.tool_id, "key": row.key, "version": row.version,
                       "risk_level": row.risk_level, "installation_id": row.installation_id,
                       "permission_id": row.permission_id, "granted_by_account_id": row.granted_by_account_id}
                      for row in tools],
            "installation": {"id": installation.id, "household_id": installation.household_id,
                             "deployment_id": installation.deployment_id,
                             "agent_version_id": installation.agent_version_id, "status": installation.status},
            "workflow": definition.get("workflow"),
            "budget": definition.get("budgets", {}),
            "children": children,
        }

    def capture(self, deployment_id, installation_id, context):
        root = self._capture_deployment(deployment_id, context.household_id, context.agent_version_id, set())
        if root["installation"]["id"] != installation_id:
            raise RuntimeError("INSTALLATION_REQUIRED")
        root.update({
            "model": {"id": context.model_id, "revision": context.model_revision,
                      "profile": dict(context.model_snapshot.get("profile", {})),
                      "snapshot": dict(context.model_snapshot)},
            "grants": {row["key"]: {"version": row["version"], "risk_level": row["risk_level"],
                                      "permission_id": row["permission_id"]} for row in root["tools"]},
            "consent": dict(context.consent),
        })
        return _freeze(root)
