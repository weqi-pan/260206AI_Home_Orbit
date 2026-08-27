import json
import time
import uuid
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import StructuredTool
from langchain.tools import ToolRuntime
from langgraph.types import Command, interrupt
from sqlalchemy import select

from backend.agent.platform.deepagents_runtime import create_secured_deep_agent
from backend.agent.platform.authority import ExecutionAuthority
from backend.agent.platform.policy import PlatformToolExecutor
from backend.agent.platform.policy_errors import PlatformToolError
from backend.agent.platform.workflow import validate_workflow
from backend.agent.platform.workflow import WorkflowExecutor
from backend.app.db.models import AgentDeployment, AgentSkillBinding, AgentToolBinding, AgentVersion, SkillVersion
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
    subagent_names: tuple = ()
    tool_executor: object = None
    graph: object = None

    def stream(self, messages, context):
        if self.workflow:
            inputs = {"message": messages[-1].get("content", "") if messages and isinstance(messages[-1], dict) else ""}
            executor = WorkflowExecutor(lambda key, args, invocation: self.tool_executor.execute(key, args, context, invocation))
            yield from executor.run(self.workflow, inputs)
            return
        agent = self.graph or create_secured_deep_agent(model=self.model, tools=self.tools, subagents=list(self.subagents),
                                          system_prompt=self.prompt, context_schema=type(context),
                                          checkpointer=self.checkpointer)
        object.__setattr__(self, "graph", agent)
        started = time.monotonic()
        tool_calls = 0
        token_count = 0
        config = {"configurable": {"thread_id": context.session_id,
                                    "checkpoint_ns": f"{context.household_id}/{context.session_id}/{context.run_id}"}}
        yield from self._stream_input(agent, {"messages": messages}, context, config)

    def _stream_input(self, agent, graph_input, context, config):
        started = time.monotonic()
        for mode, chunk in agent.stream(graph_input, stream_mode=["messages", "custom", "updates"],
                                        context=context, config=config):
            context.budget.check_time()
            if mode == "messages":
                message = chunk[0] if isinstance(chunk, tuple) else chunk
                if not isinstance(message, (AIMessageChunk, AIMessage)):
                    continue
                for call in getattr(message, "tool_calls", []) or []:
                    context.budget.consume_tool()
                    if call.get("name") == "task": context.budget.enter_delegation()
                    event = "delegate" if call.get("name") == "task" else "tool"
                    yield {"event": event, "data": {**call, "type": "tool_call"}}
                content = content_text(getattr(message, "content", ""))
                if content:
                    usage = getattr(message, "usage_metadata", None) or {}
                    context.budget.consume_tokens(usage.get("output_tokens", max(1, len(content) // 4)))
                    yield {"event": "token", "data": {"content": content}}
            elif mode == "custom" and isinstance(chunk, dict):
                yield {"event": "tool", "data": chunk}
            elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                item = chunk["__interrupt__"][0]
                yield {"event": "approval_required", "data": item.value}

    def resume(self, approval_id, context):
        if self.graph is None:
            object.__setattr__(self, "graph", create_secured_deep_agent(model=self.model, tools=self.tools,
                subagents=list(self.subagents), system_prompt=self.prompt, context_schema=type(context),
                checkpointer=self.checkpointer))
        config = {"configurable": {"thread_id": context.session_id,
                                    "checkpoint_ns": f"{context.household_id}/{context.session_id}/{context.run_id}"}}
        yield from self._stream_input(self.graph, Command(resume={"approval_id": approval_id}), context, config)
        yield {"event": "run", "data": {"run_id": context.run_id, "status": "completed"}}


class AgentCompiler:
    def __init__(self, session, checkpointer=None, tool_executor_factory=None, model_factory=None):
        self.session = session
        self.checkpointer = checkpointer
        self.tool_executor_factory = tool_executor_factory or (lambda current: PlatformToolExecutor(current))
        self.model_factory = model_factory

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
        bound_tool_ids = set(self.session.scalars(select(AgentToolBinding.tool_id).where(
            AgentToolBinding.agent_version_id == version.id, AgentToolBinding.household_id == context.household_id)).all())
        authorities = ExecutionAuthority(self.session).tools(deployment.id, context.household_id, version.id, bound_tool_ids)
        executor = self.tool_executor_factory(self.session)
        tools = []
        def execute_tool(authority, runtime, arguments):
            invocation_id = getattr(runtime, "tool_call_id", None) or str(uuid.uuid4())
            try:
                return executor.execute(authority.key, arguments, context, invocation_id)
            except __import__("backend.agent.platform.policy_errors", fromlist=["ApprovalRequired"]).ApprovalRequired as exc:
                resumed = interrupt({"approval_id": exc.approval_id, "code": "APPROVAL_REQUIRED"})
                from backend.agent.platform.approvals import ApprovalResumeService
                return ApprovalResumeService(lambda: self.session, executor.side_effect or
                    ExecutionAuthority(self.session).registry.resolve(authority.key).handler).claim_and_execute(
                        resumed.get("approval_id", exc.approval_id), context, arguments)

        for authority in authorities:
            metadata = ExecutionAuthority(self.session).registry.resolve(authority.key).metadata
            if authority.key == "platform.test_high_risk":
                def invoke_high_risk(value: str, runtime: ToolRuntime, _authority=authority):
                    return execute_tool(_authority, runtime, {"value": value})
                callable_tool = invoke_high_risk
            else:
                def invoke_no_arguments(runtime: ToolRuntime, _authority=authority):
                    return execute_tool(_authority, runtime, {})
                callable_tool = invoke_no_arguments
            tools.append(StructuredTool.from_function(callable_tool, name=authority.key, description=metadata.description))
        skill_prompt = "\n\n".join(json.loads(row.definition_json).get("instructions", "") for row in skill_rows)
        prompt = "\n\n".join(item for item in (definition.get("prompt", ""), skill_prompt) if item)
        budgets = definition.get("budgets", {})
        limits = {"tool_calls": min(int(budgets.get("tool_calls", 16)), 64),
                  "wall_time_seconds": min(int(budgets.get("wall_time_seconds", 120)), 300),
                  "tokens": min(int(budgets.get("tokens", 32000)), 100000)}
        subagents, names = [], []
        for child_id in definition.get("child_deployments", []):
            child_context = context
            child_model = self.model_factory(child_id) if self.model_factory else context.chat_model
            child_deployment = self.session.get(AgentDeployment, child_id)
            child_authorities = ExecutionAuthority(self.session).tools(
                child_id, context.household_id, child_deployment.agent_version_id)
            child_grants = {item.key: {"version": item.version, "risk_level": item.risk_level}
                            for item in child_authorities}
            child_context = type(context)(**{**context.__dict__, "deployment_id": child_id,
                                              "agent_version_id": child_deployment.agent_version_id,
                                              "grants": child_grants,
                                              "chat_model": child_model})
            child = self.compile(child_id, child_context)
            child_graph = create_secured_deep_agent(model=child.model, tools=child.tools, subagents=list(child.subagents),
                system_prompt=child.prompt, context_schema=type(context), checkpointer=self.checkpointer, name="child")
            subagents.append({"name": "child", "description": "Persisted compiled child agent", "runnable": child_graph})
            names.append("child")
        return CompiledAgent(deployment.id, version.id, prompt, workflow, tuple(tools), context.chat_model, limits,
                             self.checkpointer, tuple(subagents), tuple(names), executor)
