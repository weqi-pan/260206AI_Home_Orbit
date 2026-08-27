from pathlib import Path

from deepagents import create_deep_agent as deepagents_create
from langchain.agents.middleware import TodoListMiddleware

from backend.agent.subagents import AFTERSALES_SUBAGENT, PRESALES_SUBAGENT, REPORT_TICKET_SUBAGENT
from backend.agent.tools.agent_tools import get_current_month, get_weather
from backend.agent.tools.middleware import log_before_model, monitor_tool
from backend.model.factory import get_chat_model


PROMPTS = Path(__file__).parents[1] / "prompts"


def _prompt(name, fallback):
    path = PROMPTS / name
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def create_deep_agent():
    subagents = [PRESALES_SUBAGENT, AFTERSALES_SUBAGENT, REPORT_TICKET_SUBAGENT]
    return deepagents_create(model=get_chat_model(), tools=[get_weather, get_current_month],
                             system_prompt=_prompt("main_prompt.txt", "你是智能客服主代理，按需委派子代理。"),
                             subagents=subagents, middleware=[TodoListMiddleware(), monitor_tool, log_before_model])


def build_deep_agent(model, tools=None, subagents=None, system_prompt="你是智能客服助手。"):
    return deepagents_create(model=model, tools=tools or [], subagents=subagents or [], system_prompt=system_prompt,
                             middleware=[TodoListMiddleware(), monitor_tool, log_before_model])
