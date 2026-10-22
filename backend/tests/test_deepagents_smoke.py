from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware, wrap_tool_call
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool


@tool
def echo_tool(text: str) -> str:
    """Return the supplied text."""
    return f"echo:{text}"


@tool
def failing_tool(text: str) -> str:
    """Raise an offline failure."""
    raise RuntimeError(f"failed:{text}")


class ToolCallingChatModel(BaseChatModel):
    bound_tool_names: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "tool-calling-test-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tool_names = [item.name if hasattr(item, "name") else item["name"] for item in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        if not any(isinstance(message, ToolMessage) and message.name == "echo_tool" for message in messages):
            message = AIMessage(
                content="",
                tool_calls=[{"name": "echo_tool", "args": {"text": "offline"}, "id": "echo-1", "type": "tool_call"}],
            )
        else:
            message = AIMessage(content="offline harness complete")
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_openai_compatible_instance_constructs_real_deep_agent_without_network():
    from langchain_openai import ChatOpenAI
    from backend.llm_factory.factory import build_chat_model

    model = build_chat_model(protocol="openai_compatible", api_key="not-a-real-key", model_name="test-chat", base_url="https://api.openai.com/v1")
    agent = create_deep_agent(
        model=model,
        tools=[echo_tool],
        subagents=[{"name": "researcher", "description": "offline declarative subagent", "system_prompt": "Summarize."}],
        middleware=[TodoListMiddleware()],
    )

    assert isinstance(model, ChatOpenAI)
    assert agent.get_graph().nodes


def test_real_deep_agent_invokes_custom_tool_with_todo_subagent_and_monitor():
    monitored_tools = []

    @wrap_tool_call
    def monitor(request, handler):
        monitored_tools.append(request.tool_call["name"])
        return handler(request)

    model = ToolCallingChatModel()
    agent = create_deep_agent(
        model=model,
        tools=[echo_tool],
        subagents=[{"name": "researcher", "description": "offline declarative subagent", "system_prompt": "Summarize."}],
        middleware=[TodoListMiddleware(), monitor],
    )

    result = agent.invoke({"messages": [{"role": "user", "content": "run the offline tool"}]})

    assert "echo_tool" in model.bound_tool_names
    assert "write_todos" in model.bound_tool_names
    assert "task" in model.bound_tool_names
    assert monitored_tools == ["echo_tool"]
    assert any(isinstance(message, ToolMessage) and message.content == "echo:offline" for message in result["messages"])
    assert result["messages"][-1].content == "offline harness complete"


def test_real_deep_agent_runner_emits_tool_lifecycle_without_tool_result_as_token(monkeypatch):
    from backend.agent.deep_agent import build_deep_agent
    from backend.app.services.defaults import UnavailableAgentRunner
    import backend.agent.deep_agent as deep_agent_module

    agent = build_deep_agent(
        model=ToolCallingChatModel(),
        tools=[echo_tool],
        subagents=[{"name": "researcher", "description": "offline", "system_prompt": "Summarize."}],
    )
    monkeypatch.setattr(deep_agent_module, "create_deep_agent", lambda: agent)

    events = list(UnavailableAgentRunner().stream([{"role": "user", "content": "run"}], {}))

    tokens = [event["data"]["content"] for event in events if event["event"] == "token"]
    tool_events = [event["data"] for event in events if event["event"] == "tool"]
    assert "".join(tokens) == "offline harness complete"
    assert all("echo:offline" not in token for token in tokens)
    assert [(event["name"], event["status"]) for event in tool_events if "status" in event] == [
        ("echo_tool", "start"),
        ("echo_tool", "success"),
    ]
    assert any(event.get("type") == "tool_call" and event["name"] == "echo_tool" for event in tool_events)


def test_monitor_tool_emits_error_event_on_real_deep_agent_tool_failure():
    from backend.agent.deep_agent import build_deep_agent

    class FailingToolModel(ToolCallingChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="",
                tool_calls=[{"name": "failing_tool", "args": {"text": "offline"}, "id": "fail-1", "type": "tool_call"}],
            ))])

    agent = build_deep_agent(model=FailingToolModel(), tools=[failing_tool])
    custom_events = []
    try:
        for mode, chunk in agent.stream(
            {"messages": [{"role": "user", "content": "fail"}]}, stream_mode=["messages", "custom"]
        ):
            if mode == "custom":
                custom_events.append(chunk)
    except RuntimeError as exc:
        assert "failed:offline" in str(exc)

    assert custom_events == [
        {"name": "failing_tool", "status": "start"},
        {"name": "failing_tool", "status": "error", "message": "failed:offline"},
    ]
