from datetime import datetime

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
def _context(runtime):
    return runtime.context


def _repositories(runtime):
    repositories = runtime.context.get("repositories") if isinstance(runtime.context, dict) else runtime.context.repositories
    if not repositories:
        raise RuntimeError("工具缺少数据仓储上下文")
    return repositories


def _value(runtime, name):
    return runtime.context.get(name) if isinstance(runtime.context, dict) else getattr(runtime.context, name, None)


@tool
def get_weather(runtime: ToolRuntime) -> str:
    """获取当前用户所在城市的模拟天气。"""
    city = _value(runtime, "city")
    if not city:
        raise RuntimeError("当前用户缺少城市信息")
    return f"{city}天气晴，气温26摄氏度，适合正常使用扫地机器人。"


@tool
def get_current_month() -> str:
    """获取当前月份，格式为YYYY-MM。"""
    return datetime.now().strftime("%Y-%m")


@tool
def search_devices(keyword: str, runtime: ToolRuntime) -> str:
    """按关键词搜索设备。"""
    items = _repositories(runtime).search_devices(_value(runtime, "household_id"), keyword)
    return "\n".join(f"{x.model_id}|{x.model_name}|{x.price:.0f}元|{x.features}" for x in items) or "未找到相关设备。"


@tool
def query_device_info(model_id: str, runtime: ToolRuntime) -> str:
    """查询设备详情。"""
    x = _repositories(runtime).device(_value(runtime, "household_id"), model_id)
    return f"{x.model_id}|{x.model_name}|{x.specs}|{x.description}|{x.price:.0f}元|{x.features}" if x else "未找到设备。"


@tool
def query_inventory(model_id: str, runtime: ToolRuntime) -> str:
    """查询设备库存。"""
    rows = _repositories(runtime).inventory(_value(runtime, "household_id"), model_id)
    return "\n".join(f"{x.warehouse}:{x.stock}台" for x in rows) or "未找到库存。"


@tool
def compare_devices(model_ids: list[str], runtime: ToolRuntime) -> str:
    """以Markdown表格对比多个设备。"""
    found = {x.model_id: x for x in _repositories(runtime).devices(_value(runtime, "household_id"), model_ids)}
    lines = ["|型号|名称|价格|规格|特点|", "|---|---|---:|---|---|"]
    lines.extend(f"|{x.model_id}|{x.model_name}|{x.price:.0f}元|{x.specs}|{x.features}|" for x in (found[i] for i in model_ids if i in found))
    return "\n".join(lines)


@tool
def rag_summarize(query: str, runtime: ToolRuntime) -> str:
    """从向量知识库检索参考资料。"""
    service = _value(runtime, "vector_store_service")
    if not service:
        return "知识增强未启用，暂时无法检索知识库。"
    try:
        return "\n".join(doc.page_content for doc in service.search(query)) or "知识库中未找到相关资料。"
    except Exception as exc:
        return str(exc) or "知识库服务当前不可用。"


@tool
def search_faq(query: str, runtime: ToolRuntime) -> str:
    """搜索已审核FAQ。"""
    rows = _repositories(runtime).approved_faq(_value(runtime, "household_id"), query)
    return "\n".join(f"Q:{x.question}\nA:{x.answer}" for x in rows) or "未找到已审核FAQ。"


@tool
def fetch_external_data(month: str, runtime: ToolRuntime) -> str:
    """读取当前用户指定月份的设备使用记录。"""
    user_id = _value(runtime, "user_id")
    if not user_id:
        raise RuntimeError("当前账号未关联用户档案")
    x = _repositories(runtime).usage(_value(runtime, "household_id"), user_id, month)
    return f"用户ID:{user_id};月份:{month};特征:{x.feature};效率:{x.efficiency};耗材:{x.consumables};对比:{x.comparison}" if x else f"未找到用户ID为{user_id}在{month}的使用记录。"


@tool
def register_restock_notify(model_id: str, runtime: ToolRuntime) -> dict:
    """为当前账号登记到货通知。"""
    account_id = _value(runtime, "principal_id") or _value(runtime, "account_id")
    if not account_id:
        raise RuntimeError("缺少当前账号身份")
    item = _repositories(runtime).register_restock(_value(runtime, "household_id"), account_id, model_id)
    return {"id": item.id, "account_id": item.account_id, "model_id": item.model_id, "status": item.status}


@tool
def create_ticket(title: str, content: str, runtime: ToolRuntime) -> dict:
    """为当前账号创建客服工单。"""
    account_id = _value(runtime, "principal_id") or _value(runtime, "account_id")
    if not account_id:
        raise RuntimeError("缺少当前账号身份")
    item = _repositories(runtime).create_ticket(_value(runtime, "household_id"), account_id,
                                                _value(runtime, "session_id"), title, content)
    return {"id": item.id, "account_id": item.account_id, "title": item.title, "status": item.status}
