"""Phase 5 Fix Round 1: concurrent reminder dispatch must produce exactly one
notification and one next_run_at advance per occurrence (DB-enforced)."""
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select


def _homes(client, app):
    from backend.app.db.models import Account, HouseholdMember

    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(
            HouseholdMember.account_id == demo.id))
    return demo.id, household_id


def _due_occurrence(app, household_id, account_id, interval_minutes=60, overdue_minutes=90):
    from backend.app.db.base import utcnow
    from backend.app.services.household_daily import HouseholdTaskService, ReminderScheduleService

    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        task = HouseholdTaskService(db).create(household_id, title="并发派发测试", account_id=account_id)
        ReminderScheduleService(db).create(household_id, task_id=task.id, interval_minutes=interval_minutes,
                                           start_at=now - timedelta(minutes=overdue_minutes),
                                           account_id=account_id)
    with app.state.database.session_factory() as db:
        occurrences = ReminderScheduleService(db).due(household_id, now=now)
        assert occurrences, "schedule should be due"
        return occurrences[0], now


class _BarrierNotificationService:
    """Intercepts the exact race window: after dispatch's pre-insert existence
    check, both dispatchers wait on the barrier, then insert staggered so the
    loser's INSERT lands after the winner's COMMIT."""

    def __init__(self, session, barrier, delay, service_cls):
        self._service = service_cls(session)
        self.barrier = barrier
        self.delay = delay

    def create(self, *args, **kwargs):
        self.barrier.wait(timeout=10)
        if self.delay:
            time.sleep(self.delay)
        return self._service.create(*args, **kwargs)


def test_concurrent_dispatch_window_produces_exactly_one_notification(client, app):
    from backend.app.db.models import Notification, TaskSchedule
    from backend.app.services.household_daily import NotificationService, ReminderScheduleService

    account_id, household_id = _homes(client, app)
    occurrence, now = _due_occurrence(app, household_id, account_id)
    factory = app.state.database.session_factory
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def dispatcher(delay):
        try:
            with factory.begin() as db:
                service = ReminderScheduleService(
                    db, notifications=_BarrierNotificationService(db, barrier, delay, NotificationService))
                results.append(service.dispatch(household_id, occurrence, now=now))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    winner = threading.Thread(target=dispatcher, args=(0,))
    loser = threading.Thread(target=dispatcher, args=(0.5,))
    winner.start()
    loser.start()
    winner.join(timeout=15)
    loser.join(timeout=15)
    assert not errors, errors

    with factory() as db:
        reminders = db.scalars(select(Notification).where(
            Notification.household_id == household_id, Notification.kind == "reminder")).all()
        assert len(reminders) == 1, f"重复派发：{len(reminders)} 条提醒通知"  # F1 核心不变量
        schedule = db.scalar(select(TaskSchedule).where(
            TaskSchedule.household_id == household_id))
        expected_advance = occurrence.due_at + timedelta(minutes=schedule.interval_minutes)
        assert schedule.next_run_at == expected_advance  # 仅一次推进
        assert schedule.last_run_at == occurrence.due_at
    assert sorted(str(item["dispatched"]) for item in results) == ["False", "True"]
    assert any(item.get("reason") == "OCCURRENCE_ALREADY_DISPATCHED" for item in results)


def test_sequential_duplicate_dispatch_keeps_single_notification_and_advance(client, app):
    from backend.app.db.models import Notification, TaskSchedule
    from backend.app.services.household_daily import ReminderScheduleService

    account_id, household_id = _homes(client, app)
    occurrence, now = _due_occurrence(app, household_id, account_id)
    for _ in range(2):
        with app.state.database.session_factory.begin() as db:
            ReminderScheduleService(db).dispatch(household_id, occurrence, now=now)
    with app.state.database.session_factory() as db:
        reminders = db.scalars(select(Notification).where(
            Notification.household_id == household_id, Notification.kind == "reminder")).all()
        schedule = db.scalar(select(TaskSchedule).where(TaskSchedule.household_id == household_id))
        assert len(reminders) == 1
        assert schedule.next_run_at == occurrence.due_at + timedelta(minutes=schedule.interval_minutes)


def _alembic(command, target, database_path):
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", command, target],
                          cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)


def test_revision_0012_makes_occurrence_key_unique_and_scopes_to_reminders(tmp_path):
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    from backend.app.db.models import Base, Household, Notification

    database_path = tmp_path / "fix-round1.db"
    upgraded = _alembic("upgrade", "head", database_path)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)  # idempotent under create_all-based modes
    factory = sessionmaker(engine)
    with factory.begin() as db:
        db.add(Household(name="H", created_by_account_id="acc-1"))
        db.flush()
        household_id = db.scalar(select(Household.id))
        db.add(Notification(household_id=household_id, title="第一条", kind="reminder",
                            source_type="task_schedule", source_id="reminder:s1:2026-01-01T00:00:00",
                            occurrence_key="reminder:s1:2026-01-01T00:00:00"))
    with factory.begin() as db:
        with pytest.raises(IntegrityError):
            db.add(Notification(household_id=household_id, title="重复占位", kind="reminder",
                                source_type="task_schedule", source_id="reminder:s1:2026-01-01T00:00:00",
                                occurrence_key="reminder:s1:2026-01-01T00:00:00"))
            db.flush()
    # NULL occurrence_key 的其他通知类型不受影响（合法重复可插入）
    with factory.begin() as db:
        db.add(Notification(household_id=household_id, title="一般通知A", kind="general"))
        db.add(Notification(household_id=household_id, title="一般通知B", kind="general"))
        db.flush()
    engine.dispose()

    downgraded = _alembic("downgrade", "20260828_0011", database_path)
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    assert "occurrence_key" not in {column["name"] for column in inspect(engine).get_columns("notifications")}
    engine.dispose()

    reupgraded = _alembic("upgrade", "head", database_path)
    assert reupgraded.returncode == 0, reupgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    assert "occurrence_key" in {column["name"] for column in inspect(engine).get_columns("notifications")}
    engine.dispose()


def test_backfill_assigns_occurrence_key_to_existing_task_schedule_rows(settings, tmp_path):
    """迁移把存量提醒行的 occurrence_key 回填为 source_id，历史去重不回退。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from backend.app.db.models import Household

    database_path = tmp_path / "backfill.db"
    upgraded = _alembic("upgrade", "20260828_0011", database_path)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    factory = sessionmaker(engine)
    with factory.begin() as db:
        db.execute(text("INSERT INTO accounts (id, username, password_hash, role, status, must_change_password)"
                        " VALUES ('acc-1','legacy','x','user','active',0)"))
        db.add(Household(id="home-1", name="H", created_by_account_id="acc-1"))
        db.flush()
        db.execute(text(
            "INSERT INTO notifications (id, household_id, title, body, kind, source_type, source_id)"
            " VALUES ('n-legacy','home-1','存量提醒','','reminder','task_schedule',"
            "'reminder:s9:2026-01-01T00:00:00')"))
        db.execute(text(
            "INSERT INTO notifications (id, household_id, title, body, kind, source_type, source_id)"
            " VALUES ('n-general','home-1','一般通知','','general','','')"))
    engine.dispose()
    migrated = _alembic("upgrade", "head", database_path)
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT kind, occurrence_key FROM notifications ORDER BY kind")).all()
    engine.dispose()
    assert ("general", None) in [(kind, key) for kind, key in rows]
    assert ("reminder", "reminder:s9:2026-01-01T00:00:00") in [(kind, key) for kind, key in rows]
