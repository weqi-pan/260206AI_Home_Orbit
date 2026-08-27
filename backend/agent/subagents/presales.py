from pathlib import Path

from backend.agent.tools.agent_tools import compare_devices, query_device_info, query_inventory, search_devices


PRESALES_SUBAGENT = {
    "name": "presales",
    "description": "设备选购、详情、库存与对比顾问",
    "system_prompt": (Path(__file__).parents[2] / "prompts" / "presales_prompt.txt").read_text(encoding="utf-8"),
    "tools": [search_devices, query_device_info, query_inventory, compare_devices],
}
