import logging

from langchain.agents.middleware import before_model, wrap_tool_call
from langgraph.config import get_stream_writer


logger = logging.getLogger(__name__)


@wrap_tool_call
def monitor_tool(request, handler):
    name = request.tool_call["name"]
    writer = get_stream_writer()
    writer({"name": name, "status": "start"})
    logger.info("工具调用开始:%s", name)
    try:
        result = handler(request)
        writer({"name": name, "status": "success"})
        logger.info("工具调用成功:%s", name)
        return result
    except Exception as exc:
        writer({"name": name, "status": "error", "message": str(exc)})
        logger.exception("工具调用失败:%s", name)
        raise


@before_model
def log_before_model(state, runtime):
    logger.info("即将调用模型，消息数:%s", len(state.get("messages", [])))
