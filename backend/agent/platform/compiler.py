import json
import time
import uuid
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import StructuredTool
from langchain.tools import ToolRuntime
from langgraph.types import Command, interrupt
from langgraph.graph import END, StateGraph
from sqlalchemy import select

from backend.agent.platform.deepagents_runtime import create_secured_deep_agent
from backend.agent.platform.authority import ExecutionAuthority
from backend.agent.platform.policy import PlatformToolExecutor
from backend.agent.platform.policy_errors import PlatformToolError
from backend.agent.platform.workflow import validate_workflow
from backend.agent.platform.workflow import WorkflowExecutor
from backend.app.db.models import AgentDefinition, AgentDeployment, AgentSkillBinding, AgentToolBinding, AgentVersion, SkillVersion
from backend.app.llm_runtime.adapters import content_text


def build_platform_tool(authority, executor, context, session):
    builtin = ExecutionAuthority(session).registry.resolve(authority.key)
    metadata = builtin.metadata
    def execute(runtime, arguments):
        invocation_id = getattr(runtime, "tool_call_id", None) or str(uuid.uuid4())
        try: return executor.execute(authority.key, arguments, context, invocation_id)
        except __import__("backend.agent.platform.policy_errors", fromlist=["ApprovalRequired"]).ApprovalRequired as exc:
            resumed = interrupt({"approval_id": exc.approval_id, "code": "APPROVAL_REQUIRED"})
            from backend.agent.platform.approvals import ApprovalResumeService
            return ApprovalResumeService(lambda: session, executor.side_effect or
                ExecutionAuthority(session).registry.resolve(authority.key).handler).claim_and_execute(
                    resumed.get("approval_id", exc.approval_id), context, arguments)
    schema_name = builtin.args_schema.__name__
    if schema_name == "NoArguments":
        def callable_tool(runtime: ToolRuntime): return execute(runtime, {})
    elif schema_name == "HighRiskTestArguments":
        def callable_tool(value: str, runtime: ToolRuntime): return execute(runtime, {"value": value})
    elif schema_name == "KeywordArguments":
        def callable_tool(keyword: str, runtime: ToolRuntime): return execute(runtime, {"keyword": keyword})
    elif schema_name == "ModelArguments":
        def callable_tool(model_id: str, runtime: ToolRuntime): return execute(runtime, {"model_id": model_id})
    elif schema_name == "CompareArguments":
        def callable_tool(model_ids: list[str], runtime: ToolRuntime): return execute(runtime, {"model_ids": model_ids})
    elif schema_name == "QueryArguments":
        def callable_tool(query: str, runtime: ToolRuntime): return execute(runtime, {"query": query})
    elif schema_name == "MonthArguments":
        def callable_tool(month: str, runtime: ToolRuntime): return execute(runtime, {"month": month})
    elif schema_name == "TicketArguments":
        def callable_tool(title: str, content: str, runtime: ToolRuntime):
            return execute(runtime, {"title": title, "content": content})
    elif schema_name == "RecipeScaleArguments":
        def callable_tool(servings_base: int, servings_target: int, ingredients: list, runtime: ToolRuntime):
            return execute(runtime, {"servings_base": servings_base, "servings_target": servings_target,
                                     "ingredients": ingredients})
    elif schema_name == "ShoppingWriteArguments":
        def callable_tool(items: list, runtime: ToolRuntime):
            return execute(runtime, {"items": items})
    elif schema_name == "TaskCreateArguments":
        def callable_tool(title: str, description: str = "", due_at: str = "", runtime: ToolRuntime = None):
            return execute(runtime, {"title": title, "description": description, "due_at": due_at})
    elif schema_name == "TaskUpdateArguments":
        def callable_tool(task_id: str, title: str = "", description: str = "", due_at: str = "", status: str = "",
                          runtime: ToolRuntime = None):
            return execute(runtime, {"task_id": task_id, "title": title, "description": description,
                                     "due_at": due_at, "status": status})
    elif schema_name == "TaskConfirmArguments":
        def callable_tool(task_id: str, done: bool = True, runtime: ToolRuntime = None):
            return execute(runtime, {"task_id": task_id, "done": done})
    elif schema_name == "ReminderCreateArguments":
        def callable_tool(interval_minutes: int, task_title: str = "", task_id: str = "",
                          timezone: str = "Asia/Shanghai", missed_run_policy: str = "skip",
                          catch_up_limit: int = 1, start_at: str = "", runtime: ToolRuntime = None):
            return execute(runtime, {"interval_minutes": interval_minutes, "task_title": task_title,
                                     "task_id": task_id, "timezone": timezone,
                                     "missed_run_policy": missed_run_policy, "catch_up_limit": catch_up_limit,
                                     "start_at": start_at})
    elif schema_name == "ReminderCancelArguments":
        def callable_tool(task_id: str, runtime: ToolRuntime = None):
            return execute(runtime, {"task_id": task_id})
    elif schema_name == "MemoWriteArguments":
        def callable_tool(content: str, memo_id: str = "", runtime: ToolRuntime = None):
            return execute(runtime, {"content": content, "memo_id": memo_id})
    elif schema_name == "PreferenceReadArguments":
        def callable_tool(category: str = "", runtime: ToolRuntime = None):
            return execute(runtime, {"category": category})
    elif schema_name == "PreferenceProposeArguments":
        def callable_tool(key: str, value: str, value_type: str = "string", category: str = "general",
                          reason: str = "", runtime: ToolRuntime = None):
            return execute(runtime, {"key": key, "value": value, "value_type": value_type,
                                     "category": category, "reason": reason})
    elif schema_name == "NotificationListArguments":
        def callable_tool(unread_only: bool = True, runtime: ToolRuntime = None):
            return execute(runtime, {"unread_only": unread_only})
    else:
        raise PlatformToolError("TOOL_SCHEMA_UNSUPPORTED")
    return StructuredTool.from_function(callable_tool, name=authority.key, description=metadata.description)


class BudgetedChildRunnable:
    def __init__(self, runnable, budget, child_limits=None): self.runnable, self.budget, self.child_limits = runnable, budget, child_limits or {}
    def invoke(self, *args, **kwargs):
        with self.budget.enforce_child_limits(self.child_limits): return self.runnable.invoke(*args, **kwargs)
    def stream(self, *args, **kwargs):
        with self.budget.enforce_child_limits(self.child_limits): yield from self.runnable.stream(*args, **kwargs)
    def with_config(self, config):
        return BudgetedChildRunnable(self.runnable.with_config(config), self.budget, self.child_limits)


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

    def _workflow_graph(self, context):
        child_map = {item["name"]: item["runnable"] for item in self.subagents}
        nodes = {node["id"]: node for node in self.workflow["nodes"]}
        builder = StateGraph(dict)
        def append_event(state, event): return {**state, "events": [*state.get("events", []), event]}
        for node_id, node in nodes.items():
            kind = node["type"]
            def run_node(state, _node=node, _kind=kind):
                context.budget.check_time()
                if _kind == "call_tool":
                    context.budget.consume_tool()
                    result = self.tool_executor.execute(_node["tool"], _node.get("arguments", {}), context, _node["id"])
                    return append_event(state, {"event": "tool", "data": {"name": _node["tool"], "result": result}})
                if _kind == "respond":
                    return append_event(state, {"event": "token", "data": {"content": _node.get("text", "")}})
                if _kind == "confirm":
                    try:
                        result = self.tool_executor.execute("platform.workflow_confirm", {"message": _node["message"]},
                                                            context, _node["id"])
                    except __import__("backend.agent.platform.policy_errors", fromlist=["ApprovalRequired"]).ApprovalRequired as exc:
                        answer = interrupt({"approval_id": exc.approval_id, "code": "APPROVAL_REQUIRED",
                                            "kind": "workflow_confirm", "node_id": _node["id"],
                                            "message": _node["message"]})
                        from backend.agent.platform.approvals import ApprovalResumeService
                        result = ApprovalResumeService(lambda: self.tool_executor.session,
                            ExecutionAuthority(self.tool_executor.session).registry.resolve("platform.workflow_confirm").handler
                        ).claim_and_execute(answer.get("approval_id", exc.approval_id), context,
                                            {"message": _node["message"]})
                    context.budget.consume_tool()
                    return append_event(state, {"event": "approval_resumed", "data": result})
                if _kind == "delegate":
                    target = child_map.get(_node["subagent"])
                    if target is None: raise PlatformToolError("SUBAGENT_NOT_FOUND")
                    result = target.invoke({"messages": [{"role": "user", "content": _node["message"]}]}, context=context)
                    return append_event(state, {"event": "delegate", "data": {"subagent": _node["subagent"],
                                                                                 "result": str(result)}})
                if _kind == "parallel":
                    events = list(state.get("events", []))
                    for branch in _node["branches"]:
                        branch_node = nodes[branch]
                        if branch_node["type"] == "respond":
                            context.budget.consume_tokens(max(1, len(branch_node.get("text", "")) // 4))
                            events.append({"event": "token", "data": {"content": branch_node.get("text", "")}})
                        else: raise PlatformToolError("WORKFLOW_PARALLEL_BRANCH_INVALID")
                    return {**state, "events": events}
                return state
            builder.add_node(node_id, run_node)
        builder.set_entry_point(self.workflow["entry"])
        for node_id, node in nodes.items():
            if node["type"] == "end": builder.add_edge(node_id, END); continue
            if node["type"] == "condition":
                builder.add_conditional_edges(node_id, lambda state, n=node: n["then"] if state.get(n["field"]) == n["equals"] else n["else"],
                                              {node["then"]: node["then"], node["else"]: node["else"]})
            else:
                builder.add_edge(node_id, node.get("next", END))
        return builder.compile(checkpointer=self.checkpointer)

    def stream(self, messages, context):
        if self.workflow:
            graph = self.graph or self._workflow_graph(context); object.__setattr__(self, "graph", graph)
            config = {"configurable": {"thread_id": context.session_id,
                                        "checkpoint_ns": f"{context.household_id}/{context.session_id}/{context.run_id}"}}
            initial = {"message": messages[-1].get("content", "") if messages and isinstance(messages[-1], dict) else "",
                       "events": []}
            for update in graph.stream(initial, config=config, context=context, stream_mode="updates"):
                if "__interrupt__" in update:
                    yield {"event": "approval_required", "data": update["__interrupt__"][0].value}
                else:
                    for value in update.values():
                        for event in value.get("events", []): yield event
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
            object.__setattr__(self, "graph", self._workflow_graph(context) if self.workflow else create_secured_deep_agent(
                model=self.model, tools=self.tools, subagents=list(self.subagents), system_prompt=self.prompt,
                context_schema=type(context), checkpointer=self.checkpointer))
        config = {"configurable": {"thread_id": context.session_id,
                                    "checkpoint_ns": f"{context.household_id}/{context.session_id}/{context.run_id}"}}
        if self.workflow:
            for update in self.graph.stream(Command(resume={"approval_id": approval_id}), config=config, context=context,
                                            stream_mode="updates"):
                if "__interrupt__" in update:
                    yield {"event": "approval_required", "data": update["__interrupt__"][0].value}
                else:
                    for value in update.values():
                        for event in value.get("events", []): yield event
        else:
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
        tools = [build_platform_tool(authority, executor, context, self.session) for authority in authorities]
        skill_prompt = "\n\n".join(json.loads(row.definition_json).get("instructions", "") for row in skill_rows)
        prompt = "\n\n".join(item for item in (definition.get("prompt", ""), skill_prompt) if item)
        budgets = definition.get("budgets", {})
        limits = {"tool_calls": min(int(budgets.get("tool_calls", 16)), 64),
                  "wall_time_seconds": min(int(budgets.get("wall_time_seconds", 120)), 300),
                  "tokens": min(int(budgets.get("tokens", 32000)), 100000)}
        subagents, names = [], []
        for child_id in definition.get("child_deployments", []):
            child_context = context
            child_model = self.model_factory(child_id) if self.model_factory else None
            child_deployment = self.session.get(AgentDeployment, child_id)
            child_version = self.session.get(AgentVersion, child_deployment.agent_version_id)
            child_definition = self.session.get(AgentDefinition, child_version.agent_id)
            child_name = child_definition.name.strip().lower().replace(" ", "-")
            if not child_name or child_name in names:
                raise PlatformToolError("SUBAGENT_NAME_CONFLICT")
            child_metadata = json.loads(child_version.definition_json).get("metadata", {})
            child_description = child_metadata.get("description") or child_definition.name
            child_authorities = ExecutionAuthority(self.session).tools(
                child_id, context.household_id, child_deployment.agent_version_id)
            child_grants = {item.key: {"version": item.version, "risk_level": item.risk_level}
                            for item in child_authorities}
            child_context = type(context)(**{**context.__dict__, "deployment_id": child_id,
                                              "agent_version_id": child_deployment.agent_version_id,
                                              "grants": child_grants})
            child = self.compile(child_id, child_context)
            child_graph = create_secured_deep_agent(model=child.model, tools=child.tools, subagents=list(child.subagents),
                system_prompt=child.prompt, context_schema=type(context), checkpointer=self.checkpointer, name=child_name)
            subagents.append({"name": child_name, "description": child_description,
                              "runnable": BudgetedChildRunnable(child_graph, context.budget, child_limits=child.limits)})
            names.append(child_name)
        model = self.model_factory(deployment.id) if self.model_factory else None
        return CompiledAgent(deployment.id, version.id, prompt, workflow, tuple(tools), model, limits,
                             self.checkpointer, tuple(subagents), tuple(names), executor)
