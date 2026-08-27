from pathlib import Path

from backend.agent.tools.agent_tools import create_ticket, fetch_external_data, get_current_month, register_restock_notify


REPORT_TICKET_SUBAGENT = {
    "name": "report_ticket",
    "description": "使用报告、缺货登记与客服工单专家",
    "system_prompt": (Path(__file__).parents[2] / "prompts" / "report_prompt.txt").read_text(encoding="utf-8"),
    "tools": [get_current_month, fetch_external_data, register_restock_notify, create_ticket],
}
