import hashlib
import json

from sqlalchemy import select

from backend.agent.platform.authority import ExecutionAuthority
from backend.agent.platform.context import _freeze
from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentSkillBinding, AgentToolBinding,
                                   AgentInstallation, AgentVersion, InstallationPermission, SkillVersion, ToolDefinition)
from backend.agent.platform.policy_errors import PlatformToolError
from backend.agent.platform.versions import normalize_json, validate_agent_definition, validate_skill_definition


def _hash_json(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CompiledSnapshotService:
    def __init__(self, session):
        self.session = session

    def _capture_deployment(self, deployment_id, household_id, agent_version_id, seen, model_snapshot, consent):
        if deployment_id in seen:
            raise RuntimeError("CHILD_DEPLOYMENT_CYCLE")
        seen = {*seen, deployment_id}
        deployment, installation = ExecutionAuthority(self.session).installation(
            deployment_id, household_id, agent_version_id)
        version = self.session.get(AgentVersion, deployment.agent_version_id)
        agent = self.session.get(AgentDefinition, version.agent_id)
        definition = validate_agent_definition(json.loads(version.definition_json))
        definition_json = normalize_json(definition)
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
            children.append(self._capture_deployment(child_id, household_id, child_deployment.agent_version_id, seen,
                                                     model_snapshot, consent))
        return {
            "deployment": {"id": deployment.id, "revision": deployment.revision},
            "agent": {"id": agent.id, "name": agent.name},
            "agent_version": {"id": version.id, "version": version.version, "definition": definition,
                              "config_hash": _hash_json(definition_json)},
            "skills": [{"id": row.id, "skill_id": row.skill_id, "version": row.version,
                        "definition": validate_skill_definition(json.loads(row.definition_json)),
                        "config_hash": _hash_json(normalize_json(validate_skill_definition(json.loads(row.definition_json))))}
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
            "model": dict(model_snapshot),
            "consent": dict(consent),
            "grants": {row.key: {"version": row.version, "risk_level": row.risk_level,
                                  "permission_id": row.permission_id} for row in tools},
            "children": children,
        }

    def capture(self, deployment_id, installation_id, context):
        root = self._capture_deployment(deployment_id, context.household_id, context.agent_version_id, set(),
                                        context.model_snapshot, context.consent)
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


class SnapshotCompiler:
    def __init__(self, session, checkpointer=None, model_factory=None):
        self.session, self.checkpointer = session, checkpointer
        self.model_factory = model_factory or (lambda _deployment: None)

    def _validate_node(self, node):
        definition = validate_agent_definition(node["agent_version"]["definition"])
        canonical = normalize_json(definition)
        if _hash_json(canonical) != node["agent_version"]["config_hash"]:
            raise PlatformToolError("SNAPSHOT_HASH_INVALID")
        for skill in node.get("skills", []):
            safe = validate_skill_definition(skill["definition"])
            if _hash_json(normalize_json(safe)) != skill["config_hash"]:
                raise PlatformToolError("SNAPSHOT_HASH_INVALID")
        for child in node.get("children", []): self._validate_node(child)

    def validate_and_load(self, snapshot, context):
        if not isinstance(snapshot, dict) or snapshot.get("deployment", {}).get("id") != context.deployment_id:
            raise PlatformToolError("SNAPSHOT_INVALID")
        self._validate_node(snapshot)
        return _freeze(snapshot)

    def validate_safety(self, snapshot, context):
        deployment = self.session.get(AgentDeployment, snapshot["deployment"]["id"])
        if not deployment or deployment.household_id != context.household_id or deployment.revision != snapshot["deployment"]["revision"] or deployment.agent_version_id != snapshot["agent_version"]["id"]:
            raise PlatformToolError("VERSION_CHANGED")
        installation = self.session.get(AgentInstallation, snapshot["installation"]["id"])
        if not installation or installation.status != "installed" or installation.authority_slot != "active":
            raise PlatformToolError("VERSION_CHANGED")
        for tool in snapshot.get("tools", []):
            permission = self.session.get(InstallationPermission, tool["permission_id"])
            if not permission or permission.installation_id != installation.id or permission.tool_id != tool["tool_id"] or permission.tool_version != tool["version"]:
                raise PlatformToolError("PERMISSION_REQUIRED")
        for child in snapshot.get("children", []): self.validate_safety(child, type(context)(**{
            **context.__dict__, "deployment_id": child["deployment"]["id"],
            "agent_version_id": child["agent_version"]["id"], "deployment_revision": child["deployment"]["revision"],
            "grants": child.get("grants", {}), "compiled_snapshot": child}))
        return True

    def _rehydrate_node(self, node, context):
        from backend.agent.platform.compiler import BudgetedChildRunnable, CompiledAgent, build_platform_tool
        from backend.agent.platform.catalog import BuiltinToolRegistry
        from backend.agent.platform.policy import PlatformToolExecutor
        from backend.agent.platform.deepagents_runtime import create_secured_deep_agent
        definition = node["agent_version"]["definition"]
        executor = PlatformToolExecutor(self.session)
        tools = []
        for item in node.get("tools", []):
            authority = type("SnapshotAuthority", (), item)()
            tools.append(build_platform_tool(authority, executor, context, self.session))
        subagents, names = [], []
        for child in node.get("children", []):
            child_context = type(context)(**{**context.__dict__, "deployment_id": child["deployment"]["id"],
                "agent_version_id": child["agent_version"]["id"], "deployment_revision": child["deployment"]["revision"],
                "grants": child.get("grants", {}), "compiled_snapshot": child})
            compiled_child = self._rehydrate_node(child, child_context)
            graph = create_secured_deep_agent(model=compiled_child.model, tools=compiled_child.tools,
                subagents=list(compiled_child.subagents), system_prompt=compiled_child.prompt,
                context_schema=type(context), checkpointer=self.checkpointer, name=child["agent"]["name"])
            name = child["agent"]["name"].strip().lower().replace(" ", "-")
            subagents.append({"name": name, "description": child["agent_version"]["definition"].get("metadata", {}).get(
                "description") or child["agent"]["name"], "runnable": BudgetedChildRunnable(graph, context.budget,
                    child_limits=child.get("budget", {}))}); names.append(name)
        prompt = "\n\n".join([definition.get("prompt", ""), *[skill["definition"].get("instructions", "")
            for skill in node.get("skills", [])]]).strip()
        return CompiledAgent(node["deployment"]["id"], node["agent_version"]["id"], prompt,
            node.get("workflow"), tuple(tools), self.model_factory(node["deployment"]["id"]), node.get("budget", {}),
            self.checkpointer, tuple(subagents), tuple(names), executor)

    def rehydrate(self, snapshot, context): return self._rehydrate_node(snapshot, context)
