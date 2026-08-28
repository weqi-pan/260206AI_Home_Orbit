"""Phase 5 household daily-loop domain tests: shopping list, tasks/schedules,
structured preferences (propose → confirm) and notifications with per-account reads."""
from datetime import timedelta

import pytest
from sqlalchemy import select


@pytest.fixture
def homes(client, app):
    from backend.app.db.models import Account, HouseholdMember

    with app.state.database.session_factory.begin() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        admin = db.scalar(select(Account).where(Account.username == "admin"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        admin_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))
    return {"demo": demo.id, "admin": admin.id, "demo_home": demo_home, "admin_home": admin_home}


def _services():
    from backend.app.services.household_daily import (HouseholdTaskService, NotificationService,
                                                       PreferenceService, ReminderScheduleService,
                                                       ShoppingListService)
    return {"shopping": ShoppingListService, "tasks": HouseholdTaskService, "reminders": ReminderScheduleService,
            "notifications": NotificationService, "preferences": PreferenceService}


# ---------- shopping list ----------

def test_shopping_list_crud_and_idempotent_upsert(app, homes):
    with app.state.database.session_factory.begin() as db:
        service = _services()["shopping"](db)
        item = service.upsert(homes["demo_home"], name="鸡蛋", quantity=6, unit="个", category="食材",
                              account_id=homes["demo"])
        assert item.checked is False
        again = service.upsert(homes["demo_home"], name="鸡蛋", quantity=6, unit="个", category="食材",
                               account_id=homes["demo"])
        assert again.id == item.id and again.quantity == 6  # set-semantics → idempotent
        service.modify(homes["demo_home"], item.id, quantity=12, account_id=homes["demo"])
        service.set_checked(homes["demo_home"], item.id, True, account_id=homes["demo"])
        assert service.summary(homes["demo_home"]) == {"total": 1, "unchecked": 0, "checked": 1}
        removed = service.remove(homes["demo_home"], item.id)
        assert removed and service.summary(homes["demo_home"]) == {"total": 0, "unchecked": 0, "checked": 0}


def test_shopping_list_is_household_isolated(app, homes):
    with app.state.database.session_factory.begin() as db:
        service = _services()["shopping"](db)
        item = service.upsert(homes["demo_home"], name="牛奶", quantity=1, unit="盒", account_id=homes["demo"])
    with app.state.database.session_factory.begin() as db:
        service = _services()["shopping"](db)
        assert [row.name for row in service.list(homes["admin_home"])] == []
        with pytest.raises(LookupError):
            service.modify(homes["admin_home"], item.id, quantity=2, account_id=homes["admin"])
        with pytest.raises(LookupError):
            service.remove(homes["admin_home"], item.id)


# ---------- tasks + reminder schedules ----------

def test_task_lifecycle_and_today_view(app, homes):
    from backend.app.db.base import utcnow

    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        tasks = _services()["tasks"](db)
        task = tasks.create(homes["demo_home"], title="周末大扫除", description="客厅与厨房",
                             due_at=now + timedelta(hours=2), account_id=homes["demo"])
        overdue = tasks.create(homes["demo_home"], title="浇花", due_at=now - timedelta(hours=1),
                               account_id=homes["demo"])
        assert [row.id for row in tasks.today(homes["demo_home"])] == [overdue.id, task.id]
        tasks.complete(homes["demo_home"], task.id, account_id=homes["demo"])
        assert task.status == "done" and task.completed_by_account_id == homes["demo"]
    with app.state.database.session_factory.begin() as db:
        assert [row.title for row in _services()["tasks"](db).today(homes["demo_home"])] == ["浇花"]


def test_schedule_due_dispatch_is_idempotent_and_advances(app, homes):
    from backend.app.db.base import utcnow
    from backend.app.db.models import Notification

    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        tasks = _services()["tasks"](db)
        task = tasks.create(homes["demo_home"], title="每日垃圾分类", account_id=homes["demo"])
        schedule = _services()["reminders"](db).create(homes["demo_home"], task_id=task.id, interval_minutes=60,
                                                       timezone="Asia/Shanghai", start_at=now - timedelta(minutes=90),
                                                       account_id=homes["demo"])
        assert schedule.next_run_at is not None and schedule.active is True

    dispatched = []
    for _ in range(2):
        with app.state.database.session_factory.begin() as db:
            result = _services()["reminders"](db).dispatch_due(homes["demo_home"], now=now)
            dispatched.append(result)
    with app.state.database.session_factory() as db:
        rows = db.scalars(select(Notification).where(Notification.household_id == homes["demo_home"],
                                                     Notification.kind == "reminder")).all()
        assert len(rows) == 1  # duplicate trigger does not duplicate the notification
        schedule = db.scalars(select(_schedule_model()).where(
            _schedule_model().household_id == homes["demo_home"])).first()
        assert schedule.last_run_at is not None
        assert schedule.next_run_at > now - timedelta(minutes=90)


def test_schedule_completion_and_cancel_stop_future_runs(app, homes):
    from backend.app.db.base import utcnow

    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        tasks = _services()["tasks"](db)
        reminders = _services()["reminders"](db)
        done_task = tasks.create(homes["demo_home"], title="倒垃圾", account_id=homes["demo"])
        reminders.create(homes["demo_home"], task_id=done_task.id, interval_minutes=30, start_at=now, account_id=homes["demo"])
        cancelled = tasks.create(homes["demo_home"], title="取快递", account_id=homes["demo"])
        cancelled_schedule = reminders.create(homes["demo_home"], task_id=cancelled.id, interval_minutes=30,
                                              start_at=now, account_id=homes["demo"])
        tasks.complete(homes["demo_home"], done_task.id, account_id=homes["demo"])
        reminders.cancel(homes["demo_home"], cancelled_schedule.id, account_id=homes["demo"])
    with app.state.database.session_factory.begin() as db:
        service = _services()["reminders"](db)
        assert service.due(homes["demo_home"], now=now + timedelta(hours=3)) == []


def test_schedule_missed_run_policies_are_explicit(app, homes):
    from backend.app.db.base import utcnow
    from backend.app.db.models import Notification

    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        tasks = _services()["tasks"](db)
        reminders = _services()["reminders"](db)
        skip_task = tasks.create(homes["demo_home"], title="跳过型提醒", account_id=homes["demo"])
        catch_task = tasks.create(homes["demo_home"], title="补办型提醒", account_id=homes["demo"])
        reminders.create(homes["demo_home"], task_id=skip_task.id, interval_minutes=60,
                         start_at=now - timedelta(hours=5), account_id=homes["demo"], missed_run_policy="skip")
        reminders.create(homes["demo_home"], task_id=catch_task.id, interval_minutes=60,
                         start_at=now - timedelta(hours=5), account_id=homes["demo"],
                         missed_run_policy="catch_up_bounded", catch_up_limit=2)
    with app.state.database.session_factory.begin() as db:
        service = _services()["reminders"](db)
        occurrences = service.due(homes["demo_home"], now=now)
        skip_items = [item for item in occurrences if item.task_id == skip_task.id]
        catch_items = [item for item in occurrences if item.task_id == catch_task.id]
        assert len(skip_items) == 1  # skip：仅触发一次最新到期
        assert len(catch_items) == 2  # catch_up_bounded：补发受上限约束
        for item in occurrences:
            service.dispatch(homes["demo_home"], item, now=now)
    with app.state.database.session_factory() as db:
        rows = db.scalars(select(Notification).where(Notification.household_id == homes["demo_home"],
                                                     Notification.kind == "reminder")).all()
        assert len(rows) == 3


def _schedule_model():
    from backend.app.db.models import TaskSchedule
    return TaskSchedule


# ---------- structured preferences ----------

def test_preferences_require_member_confirmation_after_proposal(app, homes):
    service_cls = _services()["preferences"]
    with app.state.database.session_factory.begin() as db:
        service = service_cls(db)
        proposal = service.propose(homes["demo_home"], key="diet.spice_level", value="微辣", value_type="string",
                                   category="饮食", reason="家人偏好川菜", proposed_by_account_id=homes["demo"],
                                   proposed_by_kind="agent")
        assert proposal.status == "pending"
        assert service.get(homes["demo_home"], "diet.spice_level") is None  # 未确认不落库
        confirmed = service.confirm(homes["demo_home"], proposal.id, account_id=homes["demo"])
        assert confirmed.value == "微辣" and confirmed.category == "饮食"
        assert confirmed.confirmed_by_account_id == homes["demo"]
    with app.state.database.session_factory.begin() as db:
        service = service_cls(db)
        assert service.get(homes["demo_home"], "diet.spice_level").value == "微辣"
        assert service.get(homes["admin_home"], "diet.spice_level") is None  # 跨家庭不可见


def test_preference_proposals_are_typed_and_rejected_proposals_do_not_write(app, homes):
    service_cls = _services()["preferences"]
    with app.state.database.session_factory.begin() as db:
        service = service_cls(db)
        with pytest.raises(ValueError):
            service.propose(homes["demo_home"], key="home.clean_times", value="不是数字", value_type="number",
                            category="家务", proposed_by_account_id=homes["demo"], proposed_by_kind="agent")
        with pytest.raises(ValueError):
            service.propose(homes["demo_home"], key="home.bool", value="不是布尔", value_type="boolean",
                            category="家务", proposed_by_account_id=homes["demo"], proposed_by_kind="agent")
        with pytest.raises(ValueError):
            service.propose(homes["demo_home"], key="home.json", value="{bad", value_type="json",
                            category="家务", proposed_by_account_id=homes["demo"], proposed_by_kind="agent")
        proposal = service.propose(homes["demo_home"], key="home.clean_times", value="3", value_type="number",
                                   category="家务", proposed_by_account_id=homes["demo"], proposed_by_kind="agent")
        service.reject(homes["demo_home"], proposal.id, account_id=homes["demo"])
        assert service.get(homes["demo_home"], "home.clean_times") is None


def test_preference_update_keeps_updated_by_audit(app, homes):
    service_cls = _services()["preferences"]
    with app.state.database.session_factory.begin() as db:
        service = service_cls(db)
        first = service.set_confirmed(homes["demo_home"], key="diet.spice_level", value="中辣", value_type="string",
                                      category="饮食", account_id=homes["demo"])
        second = service.set_confirmed(homes["demo_home"], key="diet.spice_level", value="清淡", value_type="string",
                                       category="饮食", account_id=homes["demo"])
        assert first.id == second.id
        assert second.confirmed_by_account_id == homes["demo"]
        assert second.updated_at >= first.updated_at


# ---------- notifications ----------

def test_notifications_are_household_scoped_with_per_account_reads(app, homes):
    service_cls = _services()["notifications"]
    with app.state.database.session_factory.begin() as db:
        service = service_cls(db)
        notification = service.create(homes["demo_home"], title="今日提醒", body="倒垃圾", kind="reminder")
    with app.state.database.session_factory.begin() as db:
        service = service_cls(db)
        for account_id, expected_unread in ((homes["demo"], 1), (homes["admin"], 0)):
            rows = service.list_for_account(homes["demo_home"] if account_id == homes["demo"] else homes["admin_home"],
                                            account_id)
            assert sum(1 for row in rows if not row["read_at"]) == expected_unread
        service.mark_read(homes["demo_home"], notification.id, homes["demo"])
        rows = service.list_for_account(homes["demo_home"], homes["demo"])
        assert all(row["read_at"] for row in rows)
        assert service.unread_count(homes["demo_home"], homes["demo"]) == 0
        with pytest.raises(LookupError):
            service.mark_read(homes["admin_home"], notification.id, homes["admin"])


def test_task_and_preference_outcomes_create_notifications_via_domain_services(app, homes):
    from backend.app.db.base import utcnow
    from backend.app.db.models import Notification

    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        services = _services()
        task = services["tasks"](db).create(homes["demo_home"], title="买菜", account_id=homes["demo"])
        services["reminders"](db).create(homes["demo_home"], task_id=task.id, interval_minutes=15,
                                         start_at=now - timedelta(minutes=20), account_id=homes["demo"])
        services["reminders"](db).dispatch_due(homes["demo_home"], now=now)
        services["preferences"](db).propose(homes["demo_home"], key="diet.spice_level", value="微辣",
                                            value_type="string", category="饮食", proposed_by_account_id=homes["demo"],
                                            proposed_by_kind="agent")
    with app.state.database.session_factory() as db:
        kinds = db.scalars(select(Notification.kind).where(Notification.household_id == homes["demo_home"])).all()
        assert "reminder" in kinds and "preference_proposal" in kinds
