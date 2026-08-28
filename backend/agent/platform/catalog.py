from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from pydantic import BaseModel, ConfigDict


class UnknownToolKey(KeyError):
    pass


@dataclass(frozen=True)
class ToolMetadata:
    key: str
    display_name: str
    description: str
    risk_level: str
    version: str = "1"


@dataclass(frozen=True)
class BuiltinTool:
    metadata: ToolMetadata
    handler: Callable[[dict[str, Any], Any], Any]
    args_schema: type[BaseModel]


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HighRiskTestArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class KeywordArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: str


class ModelArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str


class CompareArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ids: list[str]


class QueryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


class MonthArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str


class TicketArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    content: str


def _current_month(_arguments, _context):
    return datetime.now().strftime("%Y-%m")


def _test_high_risk(arguments, _context):
    return {"accepted": arguments}


def _context_probe(_arguments, context):
    return f"{context.household_id}|{context.principal_id}|{context.agent_version_id}|{sorted(context.grants)}"


def _workflow_confirm(arguments, _context):
    return {"confirmed": arguments.get("message", "")}


def _repositories(context):
    if not context.repositories:
        raise RuntimeError("TOOL_REPOSITORY_REQUIRED")
    return context.repositories


def _device_search(arguments, context):
    rows = _repositories(context).search_devices(context.household_id, arguments["keyword"])
    return "\n".join(f"{row.model_id}|{row.model_name}|{row.price:.0f}元|{row.features}" for row in rows) or "未找到相关设备。"


def _device_detail(arguments, context):
    row = _repositories(context).device(context.household_id, arguments["model_id"])
    return (f"{row.model_id}|{row.model_name}|{row.specs}|{row.description}|{row.price:.0f}元|{row.features}"
            if row else "未找到设备。")


def _inventory(arguments, context):
    rows = _repositories(context).inventory(context.household_id, arguments["model_id"])
    return "\n".join(f"{row.warehouse}:{row.stock}台" for row in rows) or "未找到库存。"


def _compare(arguments, context):
    rows = {row.model_id: row for row in _repositories(context).devices(context.household_id, arguments["model_ids"])}
    lines = ["|型号|名称|价格|规格|特点|", "|---|---|---:|---|---|"]
    lines.extend(f"|{row.model_id}|{row.model_name}|{row.price:.0f}元|{row.specs}|{row.features}|"
                 for row in (rows[item] for item in arguments["model_ids"] if item in rows))
    return "\n".join(lines)


def _knowledge(arguments, context):
    rows = _repositories(context).approved_faq(context.household_id, arguments["query"])
    faq = "\n".join(f"Q:{row.question}\nA:{row.answer}" for row in rows)
    documents = []
    if context.vector_store_service:
        try:
            documents = context.vector_store_service.search(arguments["query"], household_id=context.household_id,
                                                             skill_version_ids=context.skill_version_ids)
        except Exception:
            documents = []
    vector = "\n".join(document.page_content for document in documents)
    return "\n".join(item for item in (faq, vector) if item) or "知识库中未找到相关资料。"


def _usage(arguments, context):
    row = _repositories(context).usage(context.household_id, context.user_id, arguments["month"])
    return (f"用户ID:{context.user_id};月份:{arguments['month']};特征:{row.feature};效率:{row.efficiency};"
            f"耗材:{row.consumables};对比:{row.comparison}" if row else f"未找到{arguments['month']}的使用记录。")


def _restock(arguments, context):
    row = _repositories(context).register_restock(context.household_id, context.principal_id, arguments["model_id"])
    return {"id": row.id, "model_id": row.model_id, "status": row.status}


def _ticket(arguments, context):
    row = _repositories(context).create_ticket(context.household_id, context.principal_id, context.session_id,
                                                arguments["title"], arguments["content"])
    return {"id": row.id, "title": row.title, "summary": row.summary, "status": row.status}


class WorkflowConfirmArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


class BuiltinToolRegistry:
    def __init__(self, tools: list[BuiltinTool]):
        self._tools = {tool.metadata.key: tool for tool in tools}

    @classmethod
    def default(cls):
        return cls([
            BuiltinTool(ToolMetadata("platform.current_month", "当前月份", "读取当前月份", "low"), _current_month, NoArguments),
            BuiltinTool(ToolMetadata("platform.test_high_risk", "测试高风险动作", "确定性离线副作用测试工具", "high"),
                        _test_high_risk, HighRiskTestArguments),
            BuiltinTool(ToolMetadata("platform.context_probe", "上下文探针", "读取当前运行的非敏感身份快照", "low"),
                        _context_probe, NoArguments),
            BuiltinTool(ToolMetadata("platform.workflow_confirm", "工作流确认", "确认受限工作流继续执行", "high"),
                        _workflow_confirm, WorkflowConfirmArguments),
            BuiltinTool(ToolMetadata("device.search", "设备搜索", "按关键词搜索家庭设备目录", "low"), _device_search, KeywordArguments),
            BuiltinTool(ToolMetadata("device.detail", "设备详情", "读取设备规格与功能", "low"), _device_detail, ModelArguments),
            BuiltinTool(ToolMetadata("device.inventory", "库存查询", "读取设备分仓库存", "low"), _inventory, ModelArguments),
            BuiltinTool(ToolMetadata("device.compare", "设备对比", "生成多设备 Markdown 对比表", "low"), _compare, CompareArguments),
            BuiltinTool(ToolMetadata("device.knowledge.search", "设备知识检索", "检索家庭隔离知识与已审核FAQ", "low"), _knowledge, QueryArguments),
            BuiltinTool(ToolMetadata("device.usage.report", "使用报告", "读取家庭设备月度使用报告", "low"), _usage, MonthArguments),
            BuiltinTool(ToolMetadata("device.restock.register", "到货登记", "登记可撤销的设备到货通知", "reversible"), _restock, ModelArguments),
            BuiltinTool(ToolMetadata("device.ticket.create", "创建售后工单", "创建普通售后支持工单", "reversible"), _ticket, TicketArguments),
            BuiltinTool(ToolMetadata("device.faq.search", "FAQ检索", "检索已审核设备FAQ", "low"), _knowledge, QueryArguments),
        ])

    def resolve(self, key: str) -> BuiltinTool:
        try:
            return self._tools[key]
        except KeyError as exc:
            raise UnknownToolKey(key) from exc

    def metadata(self):
        return tuple(tool.metadata for tool in self._tools.values())
