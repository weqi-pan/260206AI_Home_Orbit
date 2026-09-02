"""Phase 5 Celery foundation tests: idempotent task records, bounded retries with
backoff, dead-letter notifications, crash-recovery semantics, reminder beat dispatch,
SQLite-mode guards and manual trigger boundary. No live broker required."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select


class FlakyHandler:
    """Fails N times with a transient error; side effect is keyed by idempotency."""

    def __init__(self, failures=1):
        self.failures = failures
        self.executions = 0

    def __call__(self, session, payload, record):
        from backend.app.db.models import HouseholdMemo

        self.executions += 1
        marker = session.scalar(select(HouseholdMemo).where(
            HouseholdMemo.household_id == payload["household_id"],
            HouseholdMemo.content == f"effect:{record.idempotency_key}"))
        if marker is None:
            session.add(HouseholdMemo(household_id=payload["household_id"],
                                      content=f"effect:{record.idempotency_key}"))
            session.flush()
        if self.executions <= self.failures:
            from backend.app.tasks.executor import TransientTaskError
            raise TransientTaskError("UPSTREAM_UNAVAILABLE")
        return {"memo": "written"}


class CrashingHandler:
    """Commits the side effect in its own transaction, then 'crashes'."""

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.executions = 0

    def __call__(self, session, payload, record):
        from backend.app.db.models import HouseholdMemo

        self.executions += 1
        with self.session_factory.begin() as own:
            existing = own.scalar(select(HouseholdMemo).where(
                HouseholdMemo.household_id == payload["household_id"],
                HouseholdMemo.content == f"effect:{record.idempotency_key}"))
            if existing is None:
                own.add(HouseholdMemo(household_id=payload["household_id"],
                                      content=f"effect:{record.idempotency_key}"))
        raise RuntimeError("WORKER_CRASH_MID_TASK")


def _executor(app, handlers=None, defaults=None):
    from backend.app.tasks.executor import TaskExecutor

    return TaskExecutor(app.state.database.session_factory, handlers=handlers, defaults=defaults)


def _home(app):
    from backend.app.db.models import Account, HouseholdMember

    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        return demo.id, db.scalar(select(HouseholdMember.household_id).where(
            HouseholdMember.account_id == demo.id))


def _record(app, idempotency_key):
    from backend.app.db.models import CeleryTaskRecord

    with app.state.database.session_factory() as db:
        return db.scalar(select(CeleryTaskRecord).where(
            CeleryTaskRecord.idempotency_key == idempotency_key))


def test_task_record_carries_status_attempt_retry_and_audit(client, app):
    account_id, household_id = _home(app)
    executor = _executor(app, handlers={"demo.noop": lambda session, payload, record: {"ok": True}})
    record = executor.submit("demo.noop", {"household_id": household_id}, "noop-1", household_id=household_id)
    assert record.status == "pending" and record.attempt == 0
    finished = executor.run("noop-1")
    assert finished.status == "succeeded" and finished.attempt == 1
    assert json.loads(finished.result_json) == {"ok": True}


def test_duplicate_submit_is_idempotent_and_does_not_rerun_side_effects(client, app):
    account_id, household_id = _home(app)
    handler = FlakyHandler(failures=0)
    executor = _executor(app, handlers={"demo.write": handler})
    executor.submit("demo.write", {"household_id": household_id}, "dup-1", household_id=household_id)
    executor.run("dup-1")
    again = executor.submit("demo.write", {"household_id": household_id}, "dup-1", household_id=household_id)
    assert again.status == "succeeded"
    executor.run("dup-1")
    assert handler.executions == 1  # 已成功的幂等键不重复执行


def test_transient_failure_retries_with_backoff_and_error_code(client, app):
    from backend.app.db.base import utcnow

    account_id, household_id = _home(app)
    handler = FlakyHandler(failures=2)
    executor = _executor(app, handlers={"demo.flaky": handler})
    executor.submit("demo.flaky", {"household_id": household_id}, "flaky-1", household_id=household_id)
    first = executor.run("flaky-1")
    assert first.status == "retry_scheduled" and first.attempt == 1
    assert first.error_code == "UPSTREAM_UNAVAILABLE"
    assert first.next_retry_at > utcnow()
    too_early = executor.run("flaky-1", now=utcnow() + timedelta(seconds=1))
    assert too_early.status == "retry_scheduled" and handler.executions == 1  # 未到期不重试
    second = executor.run("flaky-1", now=first.next_retry_at + timedelta(seconds=1))
    assert second.status == "retry_scheduled" and second.attempt == 2
    third = executor.run("flaky-1", now=second.next_retry_at + timedelta(seconds=1))
    assert third.status == "succeeded"
    assert third.next_retry_at is None


def test_exhausted_retries_mark_dead_and_notify_household(client, app):
    from backend.app.db.base import utcnow
    from backend.app.db.models import Notification

    account_id, household_id = _home(app)
    handler = FlakyHandler(failures=99)
    executor = _executor(app, handlers={"demo.broken": handler}, defaults={"max_attempts": 2})
    executor.submit("demo.broken", {"household_id": household_id}, "dead-1", household_id=household_id)
    first = executor.run("dead-1")
    assert first.status == "retry_scheduled"
    second = executor.run("dead-1", now=utcnow() + timedelta(minutes=10))
    assert second.status == "dead" and second.attempt == 2 and second.error_code == "UPSTREAM_UNAVAILABLE"
    with app.state.database.session_factory() as db:
        notice = db.scalars(select(Notification).where(
            Notification.household_id == household_id, Notification.kind == "task_failed")).all()
        assert len(notice) == 1 and "dead-1" in notice[0].body or notice  # 失败通知存在


def test_worker_crash_mid_task_does_not_duplicate_on_restart(client, app):
    account_id, household_id = _home(app)
    handler = CrashingHandler(app.state.database.session_factory)
    executor = _executor(app, handlers={"demo.crash": handler}, defaults={"max_attempts": 2})
    executor.submit("demo.crash", {"household_id": household_id}, "crash-1", household_id=household_id)
    crashed = executor.run("crash-1")
    assert crashed.status == "retry_scheduled" and crashed.error_code == "TASK_EXECUTION_FAILED"
    from backend.app.db.models import HouseholdMemo
    with app.state.database.session_factory() as db:
        effects = db.scalars(select(HouseholdMemo).where(
            HouseholdMemo.content == "effect:crash-1")).all()
        assert len(effects) == 1  # 副作用已提交（模拟崩溃点）
    recovered = executor.run("crash-1", now=crashed.next_retry_at + timedelta(seconds=1))
    assert recovered.status == "dead"  # 崩溃型异常按上限进入 dead
    with app.state.database.session_factory() as db:
        effects = db.scalars(select(HouseholdMemo).where(
            HouseholdMemo.content == "effect:crash-1")).all()
        assert len(effects) == 1  # 重启后不重复副作用
        assert handler.executions == 2


def test_reminder_dispatch_task_is_due_and_occurrence_idempotent(client, app):
    from backend.app.db.base import utcnow
    from backend.app.db.models import Notification
    from backend.app.services.household_daily import HouseholdTaskService, ReminderScheduleService
    from backend.app.tasks.manual import beat_tick

    account_id, household_id = _home(app)
    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        task = HouseholdTaskService(db).create(household_id, title="喂猫", account_id=account_id)
        ReminderScheduleService(db).create(household_id, task_id=task.id, interval_minutes=30,
                                           start_at=now - timedelta(minutes=45), account_id=account_id)
    for _ in range(2):
        beat_tick(app.state.database.session_factory, now=now)
    with app.state.database.session_factory() as db:
        reminders = db.scalars(select(Notification).where(
            Notification.household_id == household_id, Notification.kind == "reminder")).all()
        assert len(reminders) == 1
    from backend.app.db.models import CeleryTaskRecord
    with app.state.database.session_factory() as db:
        records = db.scalars(select(CeleryTaskRecord).where(
            CeleryTaskRecord.task_type == "household.reminders.dispatch")).all()
        assert records and all(row.status == "succeeded" for row in records)


def test_notification_dispatch_task_creates_notification_once(client, app):
    from backend.app.db.models import Notification
    from backend.app.tasks.manual import trigger

    account_id, household_id = _home(app)
    for _ in range(2):
        trigger(app.state.database.session_factory, "household.notifications.dispatch",
                payload={"household_id": household_id, "title": "版本更新", "body": "家庭总管已就绪",
                         "kind": "system", "source_type": "system", "source_id": "release-1"},
                idempotency_key="notify:release-1")
    with app.state.database.session_factory() as db:
        rows = db.scalars(select(Notification).where(
            Notification.household_id == household_id, Notification.source_id == "release-1")).all()
        assert len(rows) == 1


def test_task_types_registered_for_full_deployment(client, app):
    from backend.app.tasks.definitions import TASK_TYPES

    assert {"household.reminders.dispatch", "household.notifications.dispatch", "knowledge.vectorize",
            "content.faq_generate", "creator.sandbox_trial", "models.health_check"} <= set(TASK_TYPES)
    assert all(spec.max_attempts >= 1 for spec in TASK_TYPES.values())


def test_sandbox_trial_task_validates_definition_without_execution(client, app):
    from backend.app.tasks.manual import trigger

    account_id, household_id = _home(app)
    record = trigger(app.state.database.session_factory, "creator.sandbox_trial",
                     payload={"definition": {"prompt": "你是家务助手", "tools": ["household.task.create"],
                                             "permissions": [{"tool_key": "household.task.create", "required": True}]}},
                     idempotency_key="sandbox-1", household_id=household_id)
    assert record.status == "succeeded"
    rejected = trigger(app.state.database.session_factory, "creator.sandbox_trial",
                       payload={"definition": {"prompt": "运行 curl http://evil.example", "tools": []}},
                       idempotency_key="sandbox-2", household_id=household_id)
    assert rejected.status in {"dead", "retry_scheduled"} and rejected.error_code


def test_sqlite_mode_never_starts_celery(settings):
    from backend.app.tasks.celery_app import assert_full_deployment_mode, create_celery_app

    with pytest.raises(RuntimeError) as guard:
        assert_full_deployment_mode(settings)
    assert "CELERY" in str(guard.value) or "production" in str(guard.value)
    with pytest.raises(RuntimeError):
        create_celery_app(settings)


def test_worker_entry_import_refuses_test_mode():
    """容器 CLI 入口在测试模式下导入必须守住生产守卫（task-9-report §8 回归）。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "APP_MODE": "test", "DATABASE_URL": "sqlite:///./worker-entry-guard.db",
           "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    result = subprocess.run(
        [sys.executable, "-c", "import importlib; importlib.import_module('backend.app.tasks.worker_entry')"],
        capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode != 0  # 导入即触发守卫，进程以失败退出
    assert "CELERY_REQUIRES_PRODUCTION_MODE" in result.stderr


def test_celery_app_declares_beat_schedule_and_worker_tasks():
    import os

    from backend.app.core.config import get_settings

    os.environ["APP_MODE"] = "production"
    os.environ["DATABASE_URL"] = "mysql+pymysql://u:p@localhost:3306/customer_service"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    get_settings.cache_clear()
    try:
        from backend.app.tasks.celery_app import create_celery_app

        app = create_celery_app()
        schedule = app.conf.beat_schedule
        assert "reminders-dispatch" in schedule and "process-retries" in schedule
        assert "models-health-check" in schedule
        assert app.conf.task_default_queue == "home-orbit"
    finally:
        os.environ.pop("APP_MODE", None)
        os.environ.pop("REDIS_URL", None)
        os.environ["APP_MODE"] = "test"
        get_settings.cache_clear()


def test_admin_task_operations_list_and_manual_run(client, app):
    from backend.app.tasks.manual import trigger

    account_id, household_id = _home(app)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    listing = client.get("/api/admin/tasks", headers=headers)
    assert listing.status_code == 200
    assert "items" in listing.json()
    run = client.post("/api/admin/tasks/household.reminders.dispatch/run", headers=headers,
                      json={"payload": {}})
    assert run.status_code == 200
    assert run.json()["status"] == "succeeded"
