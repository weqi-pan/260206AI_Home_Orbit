from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemPermission


def create_secured_deep_agent(*, model, tools, subagents, system_prompt="You are a secured platform agent.",
                              context_schema=None, checkpointer=None, name=None):
    """Construct DeepAgents without implicit subagents or host filesystem capabilities."""
    agent = create_deep_agent(
        model=model, tools=tools, subagents=subagents, system_prompt=system_prompt,
        context_schema=context_schema, checkpointer=checkpointer, name=name,
        permissions=[FilesystemPermission(operations=["read", "write", "edit", "delete", "execute"],
                                          paths=["/**"], mode="deny")],
    )
    tool_node = agent.nodes["tools"].bound
    for tool_name in ("ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"):
        tool_node._tools_by_name.pop(tool_name, None)
    return agent
