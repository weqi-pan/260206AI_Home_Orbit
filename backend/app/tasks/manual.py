"""Documented manual task-trigger boundary.

SQLite preview/test mode never runs Celery worker/beat; tests and operators use
these entrypoints to drive the very same TaskExecutor semantics synchronously:

- ``trigger(session_factory, task_type, payload, idempotency_key)``: submit + run once.
- ``process_retries(session_factory)``: run every due retry_scheduled record.
- ``beat_tick(session_factory)``: what beat does per tick — dispatch due reminders
  for every household and process due retries.
"""
from backend.app.tasks.executor import TaskExecutor


def build_executor(session_factory, handlers=None, defaults=None) -> TaskExecutor:
    return TaskExecutor(session_factory, handlers=handlers, defaults=defaults)


def trigger(session_factory, task_type, payload=None, idempotency_key=None, household_id=None, handlers=None,
            defaults=None):
    executor = build_executor(session_factory, handlers=handlers, defaults=defaults)
    key = idempotency_key or f"manual:{task_type}:{household_id or 'global'}"
    executor.submit(task_type, payload or {}, key, household_id=household_id)
    return executor.run(key)


def process_retries(session_factory, handlers=None, defaults=None):
    return build_executor(session_factory, handlers=handlers, defaults=defaults).process_due_retries()


def beat_tick(session_factory, now=None, handlers=None, defaults=None):
    executor = build_executor(session_factory, handlers=handlers, defaults=defaults)
    reminder_results = executor.dispatch_all_households("household.reminders.dispatch", now=now,
                                                        key_prefix="beat:reminders")
    retry_results = executor.process_due_retries(now=now)
    return {"reminders": reminder_results, "retries": retry_results}
