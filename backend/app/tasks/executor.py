"""Broker-independent task executor with DB-enforced idempotency and bounded retries.

Semantics:
- ``submit`` is idempotent by ``idempotency_key`` (DB unique): a second submit
  returns the existing record and never re-runs side effects.
- ``run`` executes a task handler inside one transaction together with the
  success state update; transient failures roll effects back, bump ``attempt``
  and schedule ``next_retry_at`` with exponential backoff. Exhausted retries
  (or permanent errors) mark the record ``dead`` and create a household
  notification via the domain NotificationService.
- Recovery: a record left ``running`` by a crashed worker can be re-run; the
  handler is responsible for idempotent side effects keyed by the record's
  idempotency key (reminder/notification handlers dedupe by occurrence/source).
"""
import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from backend.app.db.base import utcnow
from backend.app.db.models import CeleryTaskRecord, Household


class TransientTaskError(Exception):
    """Retryable failure carrying a stable error code."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


class PermanentTaskError(Exception):
    """Non-retryable failure carrying a stable error code."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


RETRY_BACKOFF_SECONDS = 30


class TaskExecutor:
    def __init__(self, session_factory, handlers=None, defaults=None, backoff_seconds=RETRY_BACKOFF_SECONDS):
        from backend.app.tasks.definitions import TASK_TYPES

        self.session_factory = session_factory
        self.handlers = dict(TASK_TYPES) if handlers is None else handlers
        self.defaults = defaults or {}
        self.backoff_seconds = backoff_seconds

    def submit(self, task_type, payload, idempotency_key, household_id=None) -> CeleryTaskRecord:
        with self.session_factory.begin() as session:
            existing = session.scalar(select(CeleryTaskRecord).where(
                CeleryTaskRecord.idempotency_key == idempotency_key))
            if existing is not None:
                return existing
            record = CeleryTaskRecord(idempotency_key=idempotency_key, task_type=task_type,
                                      household_id=household_id, status="pending", attempt=0,
                                      max_attempts=int(self.defaults.get("max_attempts", 3)),
                                      payload_json=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                                      audit_json=json.dumps({"submitted_at": utcnow().isoformat()}, ensure_ascii=False))
            session.add(record)
            session.flush()
            session.refresh(record)
            return record

    def _spec(self, task_type):
        spec = self.handlers.get(task_type)
        if spec is None:
            raise PermanentTaskError("TASK_TYPE_UNKNOWN")
        return spec.handler if hasattr(spec, "handler") else spec

    def _load(self, session, idempotency_key):
        record = session.scalar(select(CeleryTaskRecord).where(
            CeleryTaskRecord.idempotency_key == idempotency_key))
        if record is None:
            raise LookupError("TASK_RECORD_NOT_FOUND")
        return record

    def run(self, idempotency_key, now=None) -> CeleryTaskRecord:
        now = now or utcnow()
        with self.session_factory() as session:
            record = self._load(session, idempotency_key)
            if record.status in {"succeeded", "dead"}:
                return record
            if record.status == "retry_scheduled" and record.next_retry_at and now < record.next_retry_at:
                return record
            attempt = record.attempt + 1
            max_attempts = record.max_attempts
            payload = json.loads(record.payload_json or "{}")
            task_type = record.task_type
            household_id = record.household_id
        handler = self._spec(task_type)
        with self.session_factory.begin() as session:
            record = self._load(session, idempotency_key)
            record.status = "running"
            record.attempt = attempt
            record.updated_at = now
            session.flush()
        try:
            with self.session_factory.begin() as session:
                record = self._load(session, idempotency_key)
                result = handler(session, payload, record)
                record.status = "succeeded"
                record.result_json = json.dumps(result or {}, ensure_ascii=False, sort_keys=True)
                record.error_code = None
                record.error_detail = None
                record.next_retry_at = None
                record.updated_at = utcnow()
                audit = json.loads(record.audit_json or "{}")
                audit["finished_at"] = utcnow().isoformat()
                record.audit_json = json.dumps(audit, ensure_ascii=False, sort_keys=True)
                session.flush()
                session.refresh(record)
                return record
        except (TransientTaskError, PermanentTaskError) as exc:
            code = getattr(exc, "code", None) or "TASK_EXECUTION_FAILED"
            permanent = isinstance(exc, PermanentTaskError)
            return self._schedule_failure(idempotency_key, attempt, max_attempts, code, household_id,
                                          now=now, permanent=permanent)
        except Exception as exc:
            return self._schedule_failure(idempotency_key, attempt, max_attempts, "TASK_EXECUTION_FAILED",
                                          household_id, now=now, detail=str(exc)[:180])

    def _schedule_failure(self, idempotency_key, attempt, max_attempts, code, household_id, now, permanent=False,
                          detail=""):
        dead = permanent or attempt >= max_attempts
        with self.session_factory.begin() as session:
            record = self._load(session, idempotency_key)
            record.attempt = attempt
            record.error_code = code
            record.error_detail = detail or None
            record.updated_at = utcnow()
            if dead:
                record.status = "dead"
                record.next_retry_at = None
            else:
                record.status = "retry_scheduled"
                record.next_retry_at = now + timedelta(seconds=self.backoff_seconds * (2 ** (attempt - 1)))
            audit = json.loads(record.audit_json or "{}")
            audit.setdefault("failures", []).append({"attempt": attempt, "code": code,
                                                     "at": utcnow().isoformat()})
            record.audit_json = json.dumps(audit, ensure_ascii=False, sort_keys=True)
            if dead and record.household_id:
                from backend.app.services.household_daily import NotificationService

                NotificationService(session).create(
                    record.household_id, title="后台任务失败",
                    body=f"任务 {record.task_type}（{record.idempotency_key}）重试耗尽：{code}",
                    kind="task_failed", source_type="celery_task", source_id=record.id)
            session.flush()
            session.refresh(record)
            return record

    def process_due_retries(self, now=None):
        now = now or utcnow()
        results = []
        with self.session_factory() as session:
            keys = list(session.scalars(select(CeleryTaskRecord.idempotency_key).where(
                CeleryTaskRecord.status == "retry_scheduled", CeleryTaskRecord.next_retry_at <= now)).all())
        for key in keys:
            results.append(self.run(key, now=now))
        return results

    def dispatch_all_households(self, task_type, now=None, key_prefix=None):
        """Fan a task type out per household (used by beat for reminder dispatch)."""
        results = []
        with self.session_factory() as session:
            household_ids = list(session.scalars(select(Household.id)).all())
        for household_id in household_ids:
            key = f"{key_prefix or task_type}:{household_id}:{(now or utcnow()).replace(microsecond=0).isoformat()}"
            self.submit(task_type, {"household_id": household_id}, key, household_id=household_id)
            results.append(self.run(key, now=now))
        return results
