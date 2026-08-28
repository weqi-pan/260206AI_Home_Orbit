"""Narrow household gateways for built-in household tools.

Serialization happens inside the open session; every method is keyed by
household_id so cross-household access cannot leak rows. Writes go through the
household daily domain services - tools never touch the ORM directly.
"""
from dataclasses import dataclass
from datetime import datetime

from backend.agent.tools.gateways import ToolRepositories
from backend.app.db.models import HouseholdTask
from backend.app.services.household_daily import (HouseholdMemoService, HouseholdTaskService,
                                                  NotificationService, PreferenceService,
                                                  ReminderScheduleService, ShoppingListService)


def _parse_datetime(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError("DATETIME_INVALID") from exc


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class HouseholdRepositories:
    _sessions: object

    def shopping_list(self, household_id):
        with self._sessions() as db:
            service = ShoppingListService(db)
            rows = service.list(household_id)
            return ([{"name": row.name, "quantity": row.quantity, "unit": row.unit, "category": row.category,
                      "note": row.note, "checked": row.checked} for row in rows], service.summary(household_id))

    def shopping_write(self, household_id, items, account_id=None):
        with self._sessions.begin() as db:
            service = ShoppingListService(db)
            written = []
            for item in items:
                row = service.upsert(household_id, name=item["name"], quantity=item.get("quantity", 1),
                                     unit=item.get("unit", "份"), category=item.get("category", ""),
                                     note=item.get("note", ""), account_id=account_id)
                written.append({"name": row.name, "quantity": row.quantity, "unit": row.unit,
                                "category": row.category, "note": row.note})
            summary = service.summary(household_id)
            return {"items": written, "summary": summary}

    def task_create(self, household_id, title, description="", due_at=None, account_id=None):
        with self._sessions.begin() as db:
            row = HouseholdTaskService(db).create(household_id, title=title, description=description,
                                                  due_at=_parse_datetime(due_at), account_id=account_id)
            return {"id": row.id, "title": row.title, "status": row.status, "due_at": _iso(row.due_at)}

    def task_update(self, household_id, task_id, title=None, description=None, due_at=None, status=None,
                    account_id=None):
        with self._sessions.begin() as db:
            row = HouseholdTaskService(db).update(household_id, task_id, title=title, description=description,
                                                  due_at=_parse_datetime(due_at), status=status,
                                                  account_id=account_id)
            return {"id": row.id, "title": row.title, "status": row.status, "due_at": _iso(row.due_at)}

    def task_confirm(self, household_id, task_id, done=True, account_id=None):
        with self._sessions.begin() as db:
            service = HouseholdTaskService(db)
            if done:
                row = service.complete(household_id, task_id, account_id=account_id)
            else:
                row = service.update(household_id, task_id, status="open", account_id=account_id)
            return {"id": row.id, "title": row.title, "status": row.status, "completed_at": _iso(row.completed_at)}

    def reminder_create(self, household_id, interval_minutes, task_id="", task_title="", timezone="Asia/Shanghai",
                        missed_run_policy="skip", catch_up_limit=1, start_at=None, account_id=None):
        with self._sessions.begin() as db:
            tasks = HouseholdTaskService(db)
            if not task_id:
                if not task_title:
                    raise ValueError("REMINDER_TASK_REQUIRED")
                task = tasks.create(household_id, title=task_title, account_id=account_id)
                task_id = task.id
            schedule = ReminderScheduleService(db).create(
                household_id, task_id=task_id, interval_minutes=interval_minutes, timezone=timezone,
                start_at=_parse_datetime(start_at), missed_run_policy=missed_run_policy,
                catch_up_limit=catch_up_limit, account_id=account_id)
            task = db.get(HouseholdTask, schedule.task_id)
            return {"id": schedule.id, "task_id": schedule.task_id, "task_title": task.title if task else "",
                    "interval_minutes": schedule.interval_minutes, "timezone": schedule.timezone,
                    "missed_run_policy": schedule.missed_run_policy, "next_run_at": _iso(schedule.next_run_at),
                    "active": schedule.active}

    def reminder_cancel(self, household_id, task_id, account_id=None):
        with self._sessions.begin() as db:
            schedules = ReminderScheduleService(db).list(household_id)
            cancelled = [schedule for schedule in schedules if schedule.task_id == task_id]
            for schedule in cancelled:
                ReminderScheduleService(db).cancel(household_id, schedule.id, account_id=account_id)
            return {"task_id": task_id, "cancelled": len(cancelled), "active": False}

    def memo_write(self, household_id, content, memo_id="", account_id=None):
        with self._sessions.begin() as db:
            service = HouseholdMemoService(db)
            if memo_id:
                row = service.update(household_id, memo_id, content=content, account_id=account_id)
            else:
                row = service.create(household_id, content=content, account_id=account_id)
            return {"id": row.id, "content": row.content}

    def memo_list(self, household_id):
        with self._sessions() as db:
            rows = HouseholdMemoService(db).list(household_id)
            return [{"id": row.id, "content": row.content} for row in rows]

    def preference_list(self, household_id, category=""):
        with self._sessions() as db:
            rows = PreferenceService(db).list(household_id, category=category or None)
            return [{"key": row.key, "value": row.value, "category": row.category, "value_type": row.value_type}
                    for row in rows]

    def preference_propose(self, household_id, key, value, value_type="string", category="general", reason="",
                           account_id=None):
        with self._sessions.begin() as db:
            row = PreferenceService(db).propose(household_id, key=key, value=value, value_type=value_type,
                                                category=category, reason=reason, proposed_by_account_id=account_id,
                                                proposed_by_kind="agent")
            return {"id": row.id, "key": row.key, "value": row.value, "status": row.status,
                    "note": "已提交建议，等待家庭成员确认"}

    def notification_recent(self, household_id, account_id, unread_only=True):
        with self._sessions() as db:
            items = NotificationService(db).list_for_account(household_id, account_id, unread_only=unread_only,
                                                             limit=20)
            return [{"title": item["title"], "body": item["body"], "kind": item["kind"],
                     "created_at": item["created_at"].isoformat() if item["created_at"] else None} for item in items]

    def recipe_scale(self, servings_base, servings_target, ingredients):
        if servings_base < 1 or servings_target < 1:
            raise ValueError("SERVINGS_INVALID")
        factor = servings_target / servings_base
        lines = [f"按 {servings_target} 人份（基准 {servings_base} 人份，系数 {factor:g}）："]
        scaled = []
        for item in ingredients:
            amount = float(item.get("amount", 0)) * factor
            unit = item.get("unit", "适量")
            scaled.append({"name": item["name"], "amount": round(amount, 2), "unit": unit})
            lines.append(f"{item['name']}：{round(amount, 2)}{unit}")
        return {"factor": factor, "servings_base": servings_base, "servings_target": servings_target,
                "ingredients": scaled, "text": "\n".join(lines)}


@dataclass(frozen=True)
class HouseholdToolRepositories(HouseholdRepositories, ToolRepositories):
    """Combined narrow gateway: device + household capabilities over one session factory."""
