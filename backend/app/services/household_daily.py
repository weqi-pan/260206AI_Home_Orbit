"""Household daily-loop domain services.

Every service is household-scoped: rows are always addressed by (household_id, id)
so cross-household access raises LookupError instead of leaking data. Notification
creation from reminders, task results and preference proposals goes through
NotificationService only - no direct ORM scattering at call sites.
"""
import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from backend.app.db.base import utcnow
from backend.app.db.models import (HouseholdMemo, HouseholdPreference, HouseholdPreferenceProposal, HouseholdTask,
                                   Notification, NotificationRead, ShoppingListItem, TaskSchedule)

VALUE_TYPES = ("string", "number", "boolean", "json")


def coerce_typed_value(value: str, value_type: str):
    if value_type not in VALUE_TYPES:
        raise ValueError("PREFERENCE_VALUE_TYPE_INVALID")
    if value_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("PREFERENCE_VALUE_INVALID") from exc
    if value_type == "boolean":
        if value not in ("true", "false"):
            raise ValueError("PREFERENCE_VALUE_INVALID")
        return value == "true"
    if value_type == "json":
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("PREFERENCE_VALUE_INVALID") from exc
    return str(value)


class ShoppingListService:
    def __init__(self, session):
        self.session = session

    def list(self, household_id):
        return list(self.session.scalars(select(ShoppingListItem).where(
            ShoppingListItem.household_id == household_id).order_by(ShoppingListItem.checked, ShoppingListItem.created_at)).all())

    def summary(self, household_id):
        rows = self.list(household_id)
        return {"total": len(rows), "unchecked": sum(1 for row in rows if not row.checked),
                "checked": sum(1 for row in rows if row.checked)}

    def upsert(self, household_id, *, name, quantity=1, unit="份", category="", note="", account_id=None):
        """Set-semantics upsert keyed by (name, unit): repeating an identical write is idempotent."""
        name = str(name).strip()
        if not name:
            raise ValueError("SHOPPING_ITEM_NAME_REQUIRED")
        item = self.session.scalar(select(ShoppingListItem).where(
            ShoppingListItem.household_id == household_id, ShoppingListItem.name == name,
            ShoppingListItem.unit == unit))
        if item is None:
            item = ShoppingListItem(household_id=household_id, name=name, unit=unit,
                                    created_by_account_id=account_id)
            self.session.add(item)
        item.quantity = int(quantity)
        item.category = category or ""
        item.note = note or ""
        self.session.flush()
        return item

    def _owned(self, household_id, item_id):
        item = self.session.scalar(select(ShoppingListItem).where(
            ShoppingListItem.household_id == household_id, ShoppingListItem.id == item_id))
        if item is None:
            raise LookupError("SHOPPING_ITEM_NOT_FOUND")
        return item

    def modify(self, household_id, item_id, *, quantity=None, unit=None, category=None, note=None, name=None,
               account_id=None):
        item = self._owned(household_id, item_id)
        if name is not None and name.strip():
            item.name = name.strip()
        if quantity is not None:
            item.quantity = int(quantity)
        if unit is not None:
            item.unit = unit
        if category is not None:
            item.category = category
        if note is not None:
            item.note = note
        item.updated_at = utcnow()
        self.session.flush()
        return item

    def set_checked(self, household_id, item_id, checked, *, account_id=None):
        item = self._owned(household_id, item_id)
        item.checked = bool(checked)
        item.updated_at = utcnow()
        self.session.flush()
        return item

    def remove(self, household_id, item_id):
        item = self._owned(household_id, item_id)
        self.session.delete(item)
        self.session.flush()
        return item


class HouseholdTaskService:
    def __init__(self, session):
        self.session = session

    def create(self, household_id, *, title, description="", due_at=None, account_id=None):
        task = HouseholdTask(household_id=household_id, title=str(title).strip(), description=description or "",
                             due_at=due_at, created_by_account_id=account_id)
        self.session.add(task)
        self.session.flush()
        return task

    def _owned(self, household_id, task_id):
        task = self.session.scalar(select(HouseholdTask).where(
            HouseholdTask.household_id == household_id, HouseholdTask.id == task_id))
        if task is None:
            raise LookupError("HOUSEHOLD_TASK_NOT_FOUND")
        return task

    def update(self, household_id, task_id, *, title=None, description=None, due_at=None, status=None,
               account_id=None):
        task = self._owned(household_id, task_id)
        if title is not None and title.strip():
            task.title = title.strip()
        if description is not None:
            task.description = description
        if due_at is not None:
            task.due_at = due_at
        if status is not None:
            if status not in ("open", "done", "cancelled"):
                raise ValueError("HOUSEHOLD_TASK_STATUS_INVALID")
            task.status = status
        task.updated_at = utcnow()
        self.session.flush()
        return task

    def complete(self, household_id, task_id, *, account_id=None):
        task = self.update(household_id, task_id, status="done")
        task.completed_at = utcnow()
        task.completed_by_account_id = account_id
        for schedule in self.session.scalars(select(TaskSchedule).where(
                TaskSchedule.household_id == household_id, TaskSchedule.task_id == task_id)):
            schedule.active = False
        self.session.flush()
        return task

    def cancel(self, household_id, task_id, *, account_id=None):
        task = self.update(household_id, task_id, status="cancelled")
        for schedule in self.session.scalars(select(TaskSchedule).where(
                TaskSchedule.household_id == household_id, TaskSchedule.task_id == task_id)):
            schedule.active = False
        self.session.flush()
        return task

    def list(self, household_id, include_closed=False):
        statement = select(HouseholdTask).where(HouseholdTask.household_id == household_id)
        if not include_closed:
            statement = statement.where(HouseholdTask.status == "open")
        return list(self.session.scalars(statement.order_by(
            HouseholdTask.due_at.is_(None), HouseholdTask.due_at, HouseholdTask.created_at)).all())

    def today(self, household_id, now=None):
        now = now or utcnow()
        end = now + timedelta(days=1)
        return [task for task in self.list(household_id)
                if task.due_at is None or task.due_at < end]


@dataclass(frozen=True)
class DueOccurrence:
    schedule_id: str
    task_id: str
    task_title: str
    due_at: object
    occurrence_key: str


class ReminderScheduleService:
    """Timezone-aware interval reminders with explicit missed-run policy and idempotent dispatch."""

    def __init__(self, session, notifications=None):
        self.session = session
        self.notifications = notifications or NotificationService(session)

    def create(self, household_id, *, task_id, interval_minutes, timezone="Asia/Shanghai", start_at=None,
               missed_run_policy="skip", catch_up_limit=1, account_id=None):
        if missed_run_policy not in ("skip", "catch_up_bounded"):
            raise ValueError("MISSED_RUN_POLICY_INVALID")
        if int(interval_minutes) < 1:
            raise ValueError("INTERVAL_INVALID")
        task = self.session.scalar(select(HouseholdTask).where(
            HouseholdTask.household_id == household_id, HouseholdTask.id == task_id))
        if task is None:
            raise LookupError("HOUSEHOLD_TASK_NOT_FOUND")
        schedule = TaskSchedule(household_id=household_id, task_id=task_id, timezone=timezone,
                                interval_minutes=int(interval_minutes), next_run_at=start_at or utcnow(),
                                missed_run_policy=missed_run_policy, catch_up_limit=int(catch_up_limit),
                                active=task.status == "open", created_by_account_id=account_id)
        self.session.add(schedule)
        self.session.flush()
        return schedule

    def cancel(self, household_id, schedule_id, *, account_id=None):
        schedule = self.session.scalar(select(TaskSchedule).where(
            TaskSchedule.household_id == household_id, TaskSchedule.id == schedule_id))
        if schedule is None:
            raise LookupError("TASK_SCHEDULE_NOT_FOUND")
        schedule.active = False
        self.session.flush()
        return schedule

    def list(self, household_id):
        return list(self.session.scalars(select(TaskSchedule).where(
            TaskSchedule.household_id == household_id).order_by(TaskSchedule.created_at)).all())

    def due(self, household_id, now=None):
        """Compute due occurrences honoring the explicit missed-run policy.

        skip: at most the single latest due occurrence is dispatched, older ones are skipped.
        catch_up_bounded: up to catch_up_limit overdue occurrences are dispatched, newest first.
        """
        now = now or utcnow()
        occurrences: list[DueOccurrence] = []
        for schedule in self.session.scalars(select(TaskSchedule).where(
                TaskSchedule.household_id == household_id, TaskSchedule.active.is_(True))):
            task = self.session.get(HouseholdTask, schedule.task_id)
            if task is None or task.status != "open" or schedule.next_run_at is None:
                continue
            if schedule.next_run_at > now:
                continue
            step = timedelta(minutes=schedule.interval_minutes)
            if schedule.missed_run_policy == "catch_up_bounded":
                due_at = schedule.next_run_at
                window = [due_at]
                while window[-1] + step <= now and len(window) < schedule.catch_up_limit:
                    window.append(window[-1] + step)
                for item in window:
                    occurrences.append(self._occurrence(schedule, task, item))
            else:
                due_at = schedule.next_run_at
                while due_at + step <= now:
                    due_at += step  # skip missed occurrences; only the latest fires
                occurrences.append(self._occurrence(schedule, task, due_at))
        return occurrences

    @staticmethod
    def _occurrence(schedule, task, due_at):
        return DueOccurrence(schedule.id, task.id, task.title, due_at,
                             f"reminder:{schedule.id}:{due_at.replace(microsecond=0).isoformat()}")

    def dispatch(self, household_id, occurrence, now=None):
        """Dispatch one occurrence idempotently: duplicate triggers never duplicate notifications."""
        now = now or utcnow()
        schedule = self.session.get(TaskSchedule, occurrence.schedule_id)
        task = self.session.get(HouseholdTask, occurrence.task_id)
        if schedule is None or task is None or not schedule.active or task.status != "open":
            return {"dispatched": False, "reason": "SCHEDULE_INACTIVE"}
        existing = self.session.scalar(select(Notification).where(
            Notification.household_id == household_id, Notification.source_type == "task_schedule",
            Notification.source_id == occurrence.occurrence_key))
        if existing is not None:
            schedule.last_run_at = occurrence.due_at
            schedule.next_run_at = occurrence.due_at + timedelta(minutes=schedule.interval_minutes)
            if schedule.next_run_at <= now:
                schedule.next_run_at = now + timedelta(minutes=schedule.interval_minutes)
            self.session.flush()
            return {"dispatched": False, "reason": "OCCURRENCE_ALREADY_DISPATCHED",
                    "notification_id": existing.id, "occurrence_key": occurrence.occurrence_key}
        notification = self.notifications.create(household_id, title=f"提醒：{task.title}",
                                                 body=task.description or task.title, kind="reminder",
                                                 source_type="task_schedule", source_id=occurrence.occurrence_key)
        schedule.last_run_at = occurrence.due_at
        schedule.next_run_at = occurrence.due_at + timedelta(minutes=schedule.interval_minutes)
        if schedule.next_run_at <= now:
            schedule.next_run_at = now + timedelta(minutes=schedule.interval_minutes)
        self.session.flush()
        return {"dispatched": True, "notification_id": notification.id,
                "occurrence_key": occurrence.occurrence_key}

    def dispatch_due(self, household_id, now=None):
        results = []
        for occurrence in self.due(household_id, now=now):
            results.append(self.dispatch(household_id, occurrence, now=now))
        return results


class NotificationService:
    def __init__(self, session):
        self.session = session

    def create(self, household_id, *, title, body="", kind="general", source_type="", source_id=""):
        notification = Notification(household_id=household_id, title=title, body=body or "", kind=kind,
                                    source_type=source_type, source_id=source_id)
        self.session.add(notification)
        self.session.flush()
        return notification

    def list(self, household_id, limit=50):
        return list(self.session.scalars(select(Notification).where(
            Notification.household_id == household_id).order_by(Notification.created_at.desc()).limit(limit)).all())

    def list_for_account(self, household_id, account_id, unread_only=False, limit=50):
        rows = self.list(household_id, limit=limit)
        reads = {row.notification_id: row for row in self.session.scalars(select(NotificationRead).where(
            NotificationRead.household_id == household_id, NotificationRead.account_id == account_id)).all()}
        items = []
        for row in rows:
            read = reads.get(row.id)
            entry = {"id": row.id, "title": row.title, "body": row.body, "kind": row.kind,
                     "created_at": row.created_at, "read_at": read.read_at if read else None}
            if not unread_only or entry["read_at"] is None:
                items.append(entry)
        return items

    def unread_count(self, household_id, account_id):
        return len(self.list_for_account(household_id, account_id, unread_only=True, limit=1000))

    def mark_read(self, household_id, notification_id, account_id):
        notification = self.session.scalar(select(Notification).where(
            Notification.household_id == household_id, Notification.id == notification_id))
        if notification is None:
            raise LookupError("NOTIFICATION_NOT_FOUND")
        read = self.session.scalar(select(NotificationRead).where(
            NotificationRead.household_id == household_id, NotificationRead.notification_id == notification_id,
            NotificationRead.account_id == account_id))
        if read is None:
            read = NotificationRead(household_id=household_id, notification_id=notification_id, account_id=account_id)
            self.session.add(read)
        read.read_at = utcnow()
        self.session.flush()
        return read


class PreferenceService:
    """Structured household preferences. Agents may only propose; members confirm."""

    def __init__(self, session, notifications=None):
        self.session = session
        self.notifications = notifications or NotificationService(session)

    def list(self, household_id, category=None):
        statement = select(HouseholdPreference).where(HouseholdPreference.household_id == household_id)
        if category:
            statement = statement.where(HouseholdPreference.category == category)
        return list(self.session.scalars(statement.order_by(HouseholdPreference.key)).all())

    def get(self, household_id, key):
        return self.session.scalar(select(HouseholdPreference).where(
            HouseholdPreference.household_id == household_id, HouseholdPreference.key == key))

    def set_confirmed(self, household_id, *, key, value, value_type="string", category="general", account_id):
        coerce_typed_value(value, value_type)
        row = self.get(household_id, key)
        if row is None:
            row = HouseholdPreference(household_id=household_id, key=key, confirmed_by_account_id=account_id)
            self.session.add(row)
        row.value = str(value)
        row.value_type = value_type
        row.category = category or "general"
        row.confirmed_by_account_id = account_id
        row.updated_at = utcnow()
        self.session.flush()
        return row

    def propose(self, household_id, *, key, value, value_type="string", category="general", reason="",
                proposed_by_account_id=None, proposed_by_kind="agent"):
        coerce_typed_value(value, value_type)
        pending = self.session.scalar(select(HouseholdPreferenceProposal).where(
            HouseholdPreferenceProposal.household_id == household_id,
            HouseholdPreferenceProposal.key == key, HouseholdPreferenceProposal.status == "pending"))
        if pending is None:
            pending = HouseholdPreferenceProposal(household_id=household_id, key=key,
                                                  proposed_by_account_id=proposed_by_account_id,
                                                  proposed_by_kind=proposed_by_kind)
            self.session.add(pending)
        pending.value = str(value)
        pending.value_type = value_type
        pending.category = category or "general"
        pending.reason = reason or ""
        self.session.flush()
        self.notifications.create(household_id, title="偏好建议待确认",
                                  body=f"{key} = {value}（{reason or '无说明'}）", kind="preference_proposal",
                                  source_type="preference_proposal", source_id=pending.id)
        return pending

    def proposals(self, household_id, status=None):
        statement = select(HouseholdPreferenceProposal).where(
            HouseholdPreferenceProposal.household_id == household_id)
        if status:
            statement = statement.where(HouseholdPreferenceProposal.status == status)
        return list(self.session.scalars(statement.order_by(HouseholdPreferenceProposal.created_at.desc())).all())

    def _owned_proposal(self, household_id, proposal_id):
        proposal = self.session.scalar(select(HouseholdPreferenceProposal).where(
            HouseholdPreferenceProposal.household_id == household_id, HouseholdPreferenceProposal.id == proposal_id))
        if proposal is None:
            raise LookupError("PREFERENCE_PROPOSAL_NOT_FOUND")
        return proposal

    def confirm(self, household_id, proposal_id, *, account_id):
        proposal = self._owned_proposal(household_id, proposal_id)
        if proposal.status != "pending":
            raise LookupError("PREFERENCE_PROPOSAL_NOT_PENDING")
        row = self.set_confirmed(household_id, key=proposal.key, value=proposal.value,
                                 value_type=proposal.value_type, category=proposal.category, account_id=account_id)
        proposal.status = "confirmed"
        proposal.confirmed_by_account_id = account_id
        proposal.decided_at = utcnow()
        self.session.flush()
        self.notifications.create(household_id, title="偏好已更新",
                                  body=f"{proposal.key} = {proposal.value}", kind="preference_updated",
                                  source_type="preference_proposal", source_id=proposal.id)
        return row

    def reject(self, household_id, proposal_id, *, account_id):
        proposal = self._owned_proposal(household_id, proposal_id)
        if proposal.status != "pending":
            raise LookupError("PREFERENCE_PROPOSAL_NOT_PENDING")
        proposal.status = "rejected"
        proposal.confirmed_by_account_id = account_id
        proposal.decided_at = utcnow()
        self.session.flush()
        return proposal


class HouseholdMemoService:
    def __init__(self, session):
        self.session = session

    def list(self, household_id):
        return list(self.session.scalars(select(HouseholdMemo).where(
            HouseholdMemo.household_id == household_id).order_by(HouseholdMemo.created_at.desc())).all())

    def create(self, household_id, *, content, account_id=None):
        memo = HouseholdMemo(household_id=household_id, content=content, created_by_account_id=account_id)
        self.session.add(memo)
        self.session.flush()
        return memo

    def _owned(self, household_id, memo_id):
        memo = self.session.scalar(select(HouseholdMemo).where(
            HouseholdMemo.household_id == household_id, HouseholdMemo.id == memo_id))
        if memo is None:
            raise LookupError("HOUSEHOLD_MEMO_NOT_FOUND")
        return memo

    def update(self, household_id, memo_id, *, content, account_id=None):
        memo = self._owned(household_id, memo_id)
        memo.content = content
        memo.updated_at = utcnow()
        self.session.flush()
        return memo

    def remove(self, household_id, memo_id):
        memo = self._owned(household_id, memo_id)
        self.session.delete(memo)
        self.session.flush()
        return memo
