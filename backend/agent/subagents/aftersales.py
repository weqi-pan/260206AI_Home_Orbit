from pathlib import Path

from backend.agent.tools.agent_tools import rag_summarize, search_faq


AFTERSALES_SUBAGENT = {
    "name": "aftersales",
    "description": "售后知识和FAQ专家",
    "system_prompt": (Path(__file__).parents[2] / "prompts" / "aftersales_prompt.txt").read_text(encoding="utf-8"),
    "tools": [rag_summarize, search_faq],
}
