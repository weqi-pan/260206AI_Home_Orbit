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
    if context.vector_store_service is None:
        return "知识增强未启用，暂时无法检索设备知识。"
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


class RecipeIngredientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    amount: float = 1
    unit: str = "适量"


class RecipeScaleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servings_base: int
    servings_target: int
    ingredients: list[RecipeIngredientInput]


class ShoppingItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    quantity: int = 1
    unit: str = "份"
    category: str = ""
    note: str = ""


class ShoppingWriteArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ShoppingItemInput]


class TaskCreateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    description: str = ""
    due_at: str = ""


class TaskUpdateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    title: str = ""
    description: str = ""
    due_at: str = ""
    status: str = ""


class TaskConfirmArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    done: bool = True


class ReminderCreateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_title: str = ""
    task_id: str = ""
    interval_minutes: int
    timezone: str = "Asia/Shanghai"
    missed_run_policy: str = "skip"
    catch_up_limit: int = 1
    start_at: str = ""


class ReminderCancelArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str


class MemoWriteArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    memo_id: str = ""


class PreferenceReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = ""


class PreferenceProposeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    value: str
    value_type: str = "string"
    category: str = "general"
    reason: str = ""


class NotificationListArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unread_only: bool = True


def _household_repositories(context):
    repositories = context.repositories
    if repositories is None or not hasattr(repositories, "shopping_write"):
        raise RuntimeError("HOUSEHOLD_REPOSITORY_REQUIRED")
    return repositories


def _recipe_scale(arguments, context):
    return _household_repositories(context).recipe_scale(
        arguments["servings_base"], arguments["servings_target"], arguments.get("ingredients", []))


def _shopping_read(_arguments, context):
    repositories = _household_repositories(context)
    rows, summary = repositories.shopping_list(context.household_id)
    lines = [f"{row['name']} x{row['quantity']}{row['unit']}" + ("（已购）" if row["checked"] else "")
             for row in rows]
    lines.append(f"共 {summary['total']} 项，待购 {summary['unchecked']} 项")
    return "\n".join(lines) or "购物清单为空。"


def _shopping_write(arguments, context):
    return _household_repositories(context).shopping_write(context.household_id, arguments.get("items", []),
                                                           account_id=context.principal_id)


def _task_create(arguments, context):
    return _household_repositories(context).task_create(
        context.household_id, arguments["title"], description=arguments.get("description", ""),
        due_at=arguments.get("due_at") or None, account_id=context.principal_id)


def _task_update(arguments, context):
    return _household_repositories(context).task_update(
        context.household_id, arguments["task_id"], title=arguments.get("title") or None,
        description=arguments.get("description") or None, due_at=arguments.get("due_at") or None,
        status=arguments.get("status") or None, account_id=context.principal_id)


def _task_confirm(arguments, context):
    return _household_repositories(context).task_confirm(
        context.household_id, arguments["task_id"], done=arguments.get("done", True),
        account_id=context.principal_id)


def _reminder_create(arguments, context):
    return _household_repositories(context).reminder_create(
        context.household_id, arguments["interval_minutes"], task_id=arguments.get("task_id", ""),
        task_title=arguments.get("task_title", ""), timezone=arguments.get("timezone", "Asia/Shanghai"),
        missed_run_policy=arguments.get("missed_run_policy", "skip"),
        catch_up_limit=arguments.get("catch_up_limit", 1), start_at=arguments.get("start_at") or None,
        account_id=context.principal_id)


def _reminder_cancel(arguments, context):
    return _household_repositories(context).reminder_cancel(
        context.household_id, arguments["task_id"], account_id=context.principal_id)


def _memo_write(arguments, context):
    return _household_repositories(context).memo_write(
        context.household_id, arguments["content"], memo_id=arguments.get("memo_id", ""),
        account_id=context.principal_id)


def _preference_read(arguments, context):
    rows = _household_repositories(context).preference_list(context.household_id,
                                                            category=arguments.get("category", ""))
    lines = [f"{row['key']} = {row['value']}（{row['category']}）" for row in rows]
    return "\n".join(lines) or "暂无已确认的家庭偏好；Agent 只能提出建议，需家庭成员确认后生效。"


def _preference_propose(arguments, context):
    return _household_repositories(context).preference_propose(
        context.household_id, arguments["key"], arguments["value"],
        value_type=arguments.get("value_type", "string"), category=arguments.get("category", "general"),
        reason=arguments.get("reason", ""), account_id=context.principal_id)


def _notification_read(arguments, context):
    rows = _household_repositories(context).notification_recent(
        context.household_id, context.principal_id, unread_only=arguments.get("unread_only", True))
    lines = [f"[{row['kind']}] {row['title']}：{row['body']}" for row in rows]
    return "\n".join(lines) or "暂无未读通知。"


def _household_knowledge(arguments, context):
    """Household-isolated vector retrieval only - never an SQL/FAQ fallback."""
    if context.vector_store_service is None:
        return "知识增强未启用，暂时无法检索家庭知识。"
    try:
        documents = context.vector_store_service.search(arguments["query"], household_id=context.household_id,
                                                        skill_version_ids=context.skill_version_ids)
    except Exception:
        documents = []
    return "\n".join(document.page_content for document in documents) or "未在家庭知识中找到相关内容。"


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
            BuiltinTool(ToolMetadata("household.recipe.scale", "菜谱用量换算", "按目标人数确定性换算食材用量", "low"),
                        _recipe_scale, RecipeScaleArguments),
            BuiltinTool(ToolMetadata("household.shopping.read", "购物清单读取", "读取当前家庭购物清单与待购统计", "low"),
                        _shopping_read, NoArguments),
            BuiltinTool(ToolMetadata("household.shopping.write", "购物清单写入", "按名称新增或更新购物清单条目（可撤销）", "reversible"),
                        _shopping_write, ShoppingWriteArguments),
            BuiltinTool(ToolMetadata("household.task.create", "家庭任务创建", "创建家庭任务（可撤销）", "reversible"),
                        _task_create, TaskCreateArguments),
            BuiltinTool(ToolMetadata("household.task.update", "家庭任务更新", "更新家庭任务标题、描述或状态（可撤销）", "reversible"),
                        _task_update, TaskUpdateArguments),
            BuiltinTool(ToolMetadata("household.task.confirm", "家庭任务确认", "确认完成或重新打开家庭任务（可撤销）", "reversible"),
                        _task_confirm, TaskConfirmArguments),
            BuiltinTool(ToolMetadata("household.reminder.create", "提醒创建", "为家庭任务创建周期提醒（可撤销）", "reversible"),
                        _reminder_create, ReminderCreateArguments),
            BuiltinTool(ToolMetadata("household.reminder.cancel", "提醒取消", "取消家庭任务的周期提醒（可撤销）", "reversible"),
                        _reminder_cancel, ReminderCancelArguments),
            BuiltinTool(ToolMetadata("household.memo.write", "家庭备忘写入", "新增或更新家庭备忘（可撤销）", "reversible"),
                        _memo_write, MemoWriteArguments),
            BuiltinTool(ToolMetadata("household.preference.read", "家庭偏好读取", "读取已确认的结构化家庭偏好", "low"),
                        _preference_read, PreferenceReadArguments),
            BuiltinTool(ToolMetadata("household.preference.propose", "家庭偏好建议", "提出结构化偏好变更建议，需家庭成员确认后生效（可撤销）",
                                     "reversible"), _preference_propose, PreferenceProposeArguments),
            BuiltinTool(ToolMetadata("household.notification.read", "通知读取", "读取当前家庭站内通知", "low"),
                        _notification_read, NotificationListArguments),
            BuiltinTool(ToolMetadata("household.knowledge.search", "家庭知识检索", "在家庭隔离向量知识中检索", "low"),
                        _household_knowledge, QueryArguments),
        ])

    def resolve(self, key: str) -> BuiltinTool:
        try:
            return self._tools[key]
        except KeyError as exc:
            raise UnknownToolKey(key) from exc

    def metadata(self):
        return tuple(tool.metadata for tool in self._tools.values())
