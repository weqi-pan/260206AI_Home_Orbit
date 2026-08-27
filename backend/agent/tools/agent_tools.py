from datetime import datetime

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from sqlalchemy import func, or_, select

from backend.app.db.models import Device, DeviceUsageRecord, FaqPair, Inventory, RestockNotification, Ticket


def _factory(runtime):
    factory = runtime.context.get("session_factory")
    if not factory:
        raise RuntimeError("工具缺少数据库会话上下文")
    return factory


@tool
def get_weather(runtime: ToolRuntime) -> str:
    """获取当前用户所在城市的模拟天气。"""
    city = runtime.context.get("city")
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
    with _factory(runtime)() as db:
        pattern = f"%{keyword.strip()}%"
        items = db.scalars(select(Device).where(or_(Device.model_id.like(pattern), Device.model_name.like(pattern), Device.features.like(pattern)))).all()
        return "\n".join(f"{x.model_id}|{x.model_name}|{x.price:.0f}元|{x.features}" for x in items) or "未找到相关设备。"


@tool
def query_device_info(model_id: str, runtime: ToolRuntime) -> str:
    """查询设备详情。"""
    with _factory(runtime)() as db:
        x = db.get(Device, model_id)
        return f"{x.model_id}|{x.model_name}|{x.specs}|{x.description}|{x.price:.0f}元|{x.features}" if x else "未找到设备。"


@tool
def query_inventory(model_id: str, runtime: ToolRuntime) -> str:
    """查询设备库存。"""
    with _factory(runtime)() as db:
        rows = db.scalars(select(Inventory).where(Inventory.model_id == model_id)).all()
        return "\n".join(f"{x.warehouse}:{x.stock}台" for x in rows) or "未找到库存。"


@tool
def compare_devices(model_ids: list[str], runtime: ToolRuntime) -> str:
    """以Markdown表格对比多个设备。"""
    with _factory(runtime)() as db:
        found = {x.model_id: x for x in db.scalars(select(Device).where(Device.model_id.in_(model_ids))).all()}
        lines = ["|型号|名称|价格|规格|特点|", "|---|---|---:|---|---|"]
        lines.extend(f"|{x.model_id}|{x.model_name}|{x.price:.0f}元|{x.specs}|{x.features}|" for x in (found[i] for i in model_ids if i in found))
        return "\n".join(lines)


@tool
def rag_summarize(query: str, runtime: ToolRuntime) -> str:
    """从向量知识库检索参考资料。"""
    service = runtime.context.get("vector_store_service")
    if not service:
        return "知识库服务当前不可用。"
    return "\n".join(doc.page_content for doc in service.search(query))


@tool
def search_faq(query: str, runtime: ToolRuntime) -> str:
    """搜索已审核FAQ。"""
    with _factory(runtime)() as db:
        pattern = f"%{query}%"
        rows = db.scalars(select(FaqPair).where(FaqPair.status == "approved", or_(FaqPair.question.like(pattern), FaqPair.answer.like(pattern)))).all()
        return "\n".join(f"Q:{x.question}\nA:{x.answer}" for x in rows) or "未找到已审核FAQ。"


@tool
def fetch_external_data(month: str, runtime: ToolRuntime) -> str:
    """读取当前用户指定月份的设备使用记录。"""
    user_id = runtime.context.get("user_id")
    if not user_id:
        raise RuntimeError("当前账号未关联用户档案")
    with _factory(runtime)() as db:
        x = db.scalar(select(DeviceUsageRecord).where(DeviceUsageRecord.user_id == user_id, DeviceUsageRecord.month == month))
        return f"用户ID:{user_id};月份:{month};特征:{x.feature};效率:{x.efficiency};耗材:{x.consumables};对比:{x.comparison}" if x else f"未找到用户ID为{user_id}在{month}的使用记录。"


@tool
def register_restock_notify(model_id: str, runtime: ToolRuntime) -> dict:
    """为当前账号登记到货通知。"""
    account_id = runtime.context.get("account_id")
    if not account_id:
        raise RuntimeError("缺少当前账号身份")
    with _factory(runtime).begin() as db:
        item = db.scalar(select(RestockNotification).where(RestockNotification.account_id == account_id, RestockNotification.model_id == model_id))
        if not item:
            item = RestockNotification(account_id=account_id, model_id=model_id); db.add(item); db.flush()
        return {"id": item.id, "account_id": item.account_id, "model_id": item.model_id, "status": item.status}


@tool
def create_ticket(title: str, content: str, runtime: ToolRuntime) -> dict:
    """为当前账号创建客服工单。"""
    account_id = runtime.context.get("account_id")
    if not account_id:
        raise RuntimeError("缺少当前账号身份")
    with _factory(runtime).begin() as db:
        item = Ticket(account_id=account_id, title=title, summary=content[:200]); db.add(item); db.flush()
        return {"id": item.id, "account_id": item.account_id, "title": item.title, "status": item.status}
