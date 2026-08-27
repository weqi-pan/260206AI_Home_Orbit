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


def test_chat_tongyi_instance_constructs_real_deep_agent_without_network():
    from langchain_community.chat_models.tongyi import ChatTongyi

    from backend.model.factory import build_chat_model

    model = build_chat_model(api_key="not-a-real-key", model_name="qwen3-max")
    agent = create_deep_agent(
        model=model,
        tools=[echo_tool],
        subagents=[{"name": "researcher", "description": "offline declarative subagent", "system_prompt": "Summarize."}],
        middleware=[TodoListMiddleware()],
    )

    assert isinstance(model, ChatTongyi)
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
