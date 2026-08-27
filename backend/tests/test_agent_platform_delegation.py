from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class DelegatingModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "deterministic-delegating-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            call = {"name": "task", "args": {"description": "读取家庭上下文", "subagent_type": "child"},
                    "id": "delegate-1", "type": "tool_call"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(tool_messages[-1].content)))])


class ChildContextModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "deterministic-child-context-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        if not any(isinstance(message, ToolMessage) for message in messages):
            call = {"name": "platform.context_probe", "args": {}, "id": "probe-1", "type": "tool_call"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
        tool_message = next(message for message in messages if isinstance(message, ToolMessage))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(tool_message.content)))])


def test_real_deepagents_task_delegation_propagates_typed_run_context():
    from deepagents import create_deep_agent
    from langchain_core.tools import tool
    from langchain.tools import ToolRuntime
    from backend.agent.platform.context import RunContext

    @tool("platform.context_probe")
    def context_probe(runtime: ToolRuntime[RunContext]) -> str:
        """Return immutable platform context fields."""
        context = runtime.context
        return f"{context.household_id}|{context.principal_id}|{context.agent_version_id}|{sorted(context.grants)}"

    child = create_deep_agent(model=ChildContextModel(), tools=[context_probe], system_prompt="child",
                              context_schema=RunContext, name="child")
    orchestrator = create_deep_agent(
        model=DelegatingModel(), tools=[], system_prompt="delegate", context_schema=RunContext,
        subagents=[{"name": "child", "description": "context child", "runnable": child}],
    )
    context = RunContext(principal_id="principal-1", household_id="household-1", member_id="member-1", role="owner",
                         session_id="session-1", deployment_id="deployment-1", agent_version_id="version-1",
                         run_id="run-1", timezone="Asia/Shanghai", grants={"platform.context_probe": {"version": "1"}}, consent={})

    result = orchestrator.invoke({"messages": [{"role": "user", "content": "delegate"}]}, context=context)

    assert "household-1|principal-1|version-1|['platform.context_probe']" in result["messages"][-1].content


def test_platform_runner_preserves_token_tool_and_adds_run_delegate_events():
    from backend.agent.platform.runner import PlatformAgentRunner
    from backend.agent.platform.context import RunContext

    class FakeCompiled:
        def stream(self, _messages, _context):
            yield {"event": "delegate", "data": {"agent": "child"}}
            yield {"event": "tool", "data": {"name": "platform.current_month", "status": "success"}}
            yield {"event": "token", "data": {"content": "ok"}}

    context = RunContext(principal_id="p", household_id="h", member_id="m", role="owner", session_id="s",
                         deployment_id="d", agent_version_id="v", run_id="r", timezone="UTC", grants={}, consent={})
    events = list(PlatformAgentRunner(lambda _context: FakeCompiled()).stream([], context))
    assert [event["event"] for event in events] == ["run", "delegate", "tool", "token", "run"]
    assert events[0]["data"]["status"] == "started"
    assert events[-1]["data"]["status"] == "completed"


def test_secured_deep_agent_disables_implicit_general_purpose_and_host_filesystem():
    from backend.agent.platform.deepagents_runtime import create_secured_deep_agent

    agent = create_secured_deep_agent(model=ChildContextModel(), tools=[], subagents=[])
    tool_names = set(agent.nodes["tools"].bound._tools_by_name)
    assert "general-purpose" not in str(agent)
    assert {"execute", "write_file", "edit_file", "delete"}.isdisjoint(tool_names)
