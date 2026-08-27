from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware


def build_deep_agent(model, tools=None, subagents=None, system_prompt: str = "你是智能客服助手。"):
    return create_deep_agent(
        model=model,
        tools=tools or [],
        system_prompt=system_prompt,
        subagents=subagents or [],
        middleware=[TodoListMiddleware()],
    )
