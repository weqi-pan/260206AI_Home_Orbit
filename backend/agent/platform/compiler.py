import json
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import StructuredTool
from sqlalchemy import select

from backend.agent.platform.deepagents_runtime import create_secured_deep_agent
from backend.agent.platform.policy import PlatformToolExecutor
from backend.agent.platform.workflow import validate_workflow
from backend.app.db.models import AgentDeployment, AgentSkillBinding, AgentToolBinding, AgentVersion, SkillVersion, ToolDefinition, ToolPermission
from backend.app.models.adapters import content_text


@dataclass(frozen=True)
class CompiledAgent:
    deployment_id: str
    agent_version_id: str
    prompt: str
    workflow: dict | None
    tools: tuple
    model: object
    limits: dict
    checkpointer: object = None
    subagents: tuple = ()

    def stream(self, messages, context):
        agent = create_secured_deep_agent(model=self.model, tools=self.tools, subagents=list(self.subagents),
                                          system_prompt=self.prompt, context_schema=type(context),
                                          checkpointer=self.checkpointer)
        started = time.monotonic()
        tool_calls = 0
        token_count = 0
        config = {"configurable": {"thread_id": context.session_id,
                                    "checkpoint_ns": f"{context.household_id}/{context.session_id}/{context.run_id}"}}
        for mode, chunk in agent.stream({"messages": messages}, stream_mode=["messages", "custom"],
                                        context=context, config=config):
            if time.monotonic() - started > self.limits["wall_time_seconds"]:
                raise RuntimeError("RUN_LIMIT_EXCEEDED")
            if mode == "messages":
                message = chunk[0] if isinstance(chunk, tuple) else chunk
                if not isinstance(message, (AIMessageChunk, AIMessage)):
                    continue
                for call in getattr(message, "tool_calls", []) or []:
                    tool_calls += 1
                    if tool_calls > self.limits["tool_calls"]:
                        raise RuntimeError("RUN_LIMIT_EXCEEDED")
                    event = "delegate" if call.get("name") == "task" else "tool"
                    yield {"event": event, "data": {**call, "type": "tool_call"}}
                content = content_text(getattr(message, "content", ""))
                if content:
                    token_count += len(content)
                    if token_count > self.limits["tokens"]:
                        raise RuntimeError("RUN_LIMIT_EXCEEDED")
                    yield {"event": "token", "data": {"content": content}}
            elif mode == "custom" and isinstance(chunk, dict):
                yield {"event": "tool", "data": chunk}


class AgentCompiler:
    def __init__(self, session, checkpointer=None):
        self.session = session
        self.checkpointer = checkpointer

    def compile(self, deployment_id, context):
        deployment = self.session.scalar(select(AgentDeployment).where(
            AgentDeployment.id == deployment_id, AgentDeployment.household_id == context.household_id))
        if not deployment or deployment.agent_version_id != context.agent_version_id:
            raise RuntimeError("PERMISSION_REQUIRED")
        version = self.session.get(AgentVersion, deployment.agent_version_id)
        definition = json.loads(version.definition_json)
        workflow = validate_workflow(definition.get("workflow"))
        skill_rows = self.session.scalars(select(SkillVersion).join(
            AgentSkillBinding, AgentSkillBinding.skill_version_id == SkillVersion.id
        ).where(AgentSkillBinding.agent_version_id == version.id,
                SkillVersion.household_id == context.household_id)).all()
        rows = self.session.execute(select(ToolDefinition).join(AgentToolBinding, AgentToolBinding.tool_id == ToolDefinition.id).join(
            ToolPermission, (ToolPermission.tool_id == ToolDefinition.id) &
            (ToolPermission.household_id == context.household_id) &
            (ToolPermission.tool_version == ToolDefinition.version)
        ).where(AgentToolBinding.agent_version_id == version.id, ToolDefinition.enabled.is_(True))).scalars().all()
        executor = PlatformToolExecutor(self.session)
        tools = []
        for row in rows:
            def invoke(_row=row, **arguments):
                return executor.execute(_row.key, arguments, context, f"{context.run_id}:{_row.key}:{len(arguments)}")
            tools.append(StructuredTool.from_function(invoke, name=row.key, description=row.description or row.display_name))
        skill_prompt = "\n\n".join(json.loads(row.definition_json).get("instructions", "") for row in skill_rows)
        prompt = "\n\n".join(item for item in (definition.get("prompt", ""), skill_prompt) if item)
        budgets = definition.get("budgets", {})
        limits = {"tool_calls": min(int(budgets.get("tool_calls", 16)), 64),
                  "wall_time_seconds": min(int(budgets.get("wall_time_seconds", 120)), 300),
                  "tokens": min(int(budgets.get("tokens", 32000)), 100000)}
        return CompiledAgent(deployment.id, version.id, prompt, workflow, tuple(tools), context.chat_model, limits,
                             self.checkpointer)
