"""Phase 5 household built-in tool catalog and gateway tests."""
import pytest
from sqlalchemy import select

from backend.agent.platform.catalog import BuiltinToolRegistry
from backend.agent.platform.context import RunContext


HOUSEHOLD_TOOLS = {
    "household.recipe.scale": ("low", "1"),
    "household.shopping.read": ("low", "1"),
    "household.shopping.write": ("reversible", "1"),
    "household.task.create": ("reversible", "1"),
    "household.task.update": ("reversible", "1"),
    "household.task.confirm": ("reversible", "1"),
    "household.reminder.create": ("reversible", "1"),
    "household.reminder.cancel": ("reversible", "1"),
    "household.memo.write": ("reversible", "1"),
    "household.preference.read": ("low", "1"),
    "household.preference.propose": ("reversible", "1"),
    "household.notification.read": ("low", "1"),
    "household.knowledge.search": ("low", "1"),
}


def test_household_tools_registered_with_exact_metadata_and_risk():
    registry = BuiltinToolRegistry.default()
    for key, (risk, version) in HOUSEHOLD_TOOLS.items():
        metadata = registry.resolve(key).metadata
        assert metadata.risk_level == risk, key
        assert metadata.version == version, key
        assert metadata.display_name and metadata.description


def test_no_household_tool_is_high_risk_or_destructive():
    registry = BuiltinToolRegistry.default()
    household = [item for item in registry.metadata() if item.key.startswith("household.")]
    assert {item.risk_level for item in household} <= {"low", "reversible"}


@pytest.fixture
def homes(client, app):
    from backend.app.db.models import Account, HouseholdMember

    with app.state.database.session_factory.begin() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        admin = db.scalar(select(Account).where(Account.username == "admin"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        admin_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))
    return {"demo": demo.id, "admin": admin.id, "demo_home": demo_home, "admin_home": admin_home}


def _context(app, homes, **overrides):
    from backend.agent.tools.household_gateways import HouseholdToolRepositories

    base = dict(principal_id=homes["demo"], household_id=homes["demo_home"], member_id="member", role="owner",
                session_id="session", deployment_id="deployment", agent_version_id="version", run_id="run",
                timezone="Asia/Shanghai", grants={}, consent={},
                repositories=HouseholdToolRepositories(app.state.database.session_factory))
    base.update(overrides)
    return RunContext(**base)


def _handler(key):
    return BuiltinToolRegistry.default().resolve(key).handler


def test_recipe_scale_is_deterministic_per_person(app, homes):
    result = _handler("household.recipe.scale")({
        "servings_base": 2, "servings_target": 5,
        "ingredients": [{"name": "鸡蛋", "amount": 4, "unit": "个"}, {"name": "生抽", "amount": 10, "unit": "ml"}],
    }, _context(app, homes))
    text = result if isinstance(result, str) else str(result)
    assert "鸡蛋" in text and "10" in text  # 4*5/2=10
    assert "生抽" in text and "25" in text  # 10*5/2=25


def test_shopping_write_is_idempotent_set_semantics(app, homes):
    context = _context(app, homes)
    payload = {"items": [{"name": "鸡蛋", "quantity": 6, "unit": "个", "category": "食材"}]}
    first = _handler("household.shopping.write")(payload, context)
    second = _handler("household.shopping.write")(payload, context)
    assert first["summary"]["total"] == 1 and second["summary"]["total"] == 1
    assert [item["quantity"] for item in second["items"]] == [6]


def test_shopping_and_tasks_are_household_isolated(app, homes):
    context = _context(app, homes)
    _handler("household.shopping.write")({"items": [{"name": "鸡蛋", "quantity": 6, "unit": "个"}]}, context)
    other = _context(app, homes, household_id=homes["admin_home"], principal_id=homes["admin"])
    read = _handler("household.shopping.read")({}, other)
    assert "鸡蛋" not in str(read)
    task = _handler("household.task.create")({"title": "浇花"}, context)
    with pytest.raises(Exception):
        _handler("household.task.update")({"task_id": task["id"], "title": "越权"}, other)


def test_reminder_tools_create_and_cancel_schedules(app, homes):
    context = _context(app, homes)
    created = _handler("household.reminder.create")(
        {"task_title": "每日垃圾分类", "interval_minutes": 60, "missed_run_policy": "skip"}, context)
    assert created["interval_minutes"] == 60 and created["active"] is True
    cancelled = _handler("household.reminder.cancel")({"task_id": created["task_id"]}, context)
    assert cancelled["active"] is False


def test_preference_tool_is_propose_only_never_writes_preference(app, homes):
    from backend.app.db.models import HouseholdPreference, HouseholdPreferenceProposal

    context = _context(app, homes)
    proposal = _handler("household.preference.propose")(
        {"key": "diet.spice_level", "value": "微辣", "value_type": "string", "category": "饮食",
         "reason": "家人偏好"}, context)
    assert proposal["status"] == "pending"
    with app.state.database.session_factory() as db:
        assert db.scalar(select(HouseholdPreference).where(
            HouseholdPreference.household_id == homes["demo_home"])) is None
        assert db.scalar(select(HouseholdPreferenceProposal).where(
            HouseholdPreferenceProposal.id == proposal["id"], HouseholdPreferenceProposal.status == "pending"))
    reading = _handler("household.preference.read")({"category": ""}, context)
    assert "diet.spice_level" not in str(reading)


def test_notification_tool_reads_household_notifications(app, homes):
    context = _context(app, homes)
    from backend.app.services.household_daily import NotificationService
    with app.state.database.session_factory.begin() as db:
        NotificationService(db).create(homes["demo_home"], title="仅本家庭", kind="reminder")
    result = _handler("household.notification.read")({"unread_only": True}, context)
    assert "仅本家庭" in str(result)
    other = _context(app, homes, household_id=homes["admin_home"], principal_id=homes["admin"])
    assert "仅本家庭" not in str(_handler("household.notification.read")({"unread_only": True}, other))


def test_knowledge_search_uses_vector_scope_only_without_sql_fallback(app, homes):
    from backend.app.db.models import FaqPair
    from backend.app.db.base import utcnow
    from langchain_core.documents import Document

    class ScopedVectorStore:
        def __init__(self, corpus):
            self.corpus = corpus

        def search(self, query, k=4, household_id=None, skill_version_ids=()):
            return [Document(page_content=text) for text in self.corpus.get(household_id, [])]

    with app.state.database.session_factory.begin() as db:
        db.add(FaqPair(household_id=homes["demo_home"], question="红烧肉怎么做？", answer="SQL常见问题答案",
                       status="approved", reviewed_at=utcnow()))
    context = _context(app, homes, vector_store_service=ScopedVectorStore({
        homes["admin_home"]: ["admin专属知识"]}))
    result = _handler("household.knowledge.search")({"query": "红烧肉"}, context)
    assert "未" in str(result) and "admin专属知识" not in str(result) and "SQL常见问题答案" not in str(result)
    context = _context(app, homes, vector_store_service=ScopedVectorStore({
        homes["demo_home"]: ["家庭知识：红烧肉需要焯水"]}))
    result = _handler("household.knowledge.search")({"query": "红烧肉"}, context)
    assert "红烧肉需要焯水" in str(result)


def test_knowledge_tools_degrade_without_embedding_and_non_knowledge_tools_still_work(app, homes):
    context = _context(app, homes, vector_store_service=None)

    household = _handler("household.knowledge.search")({"query": "红烧肉"}, context)
    device = _handler("device.knowledge.search")({"query": "保修"}, context)
    inventory = _handler("device.inventory")({"model_id": "R1-Lite"}, context)

    assert household == "知识增强未启用，暂时无法检索家庭知识。"
    assert device == "知识增强未启用，暂时无法检索设备知识。"
    assert "台" in inventory
