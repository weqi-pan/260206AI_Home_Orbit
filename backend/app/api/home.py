from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.households import _membership
from backend.app.api.setup import setup_status
from backend.app.core.security import get_current_account, get_db
from backend.app.db.base import utcnow
from backend.app.db.models import (Account, Device, HouseholdMember, Inventory, ShoppingListItem)
from backend.app.services.household_daily import (HouseholdMemoService, HouseholdTaskService, NotificationService,
                                                  PreferenceService, ReminderScheduleService, ShoppingListService)


router = APIRouter(prefix="/home", tags=["home"])


def _parse_datetime(value: str | None):
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "DATETIME_INVALID"}) from exc


class OrbitNode(BaseModel):
    id: str
    label: str
    kind: str
    status: str
    href: str
    detail: str


class OrbitResponse(BaseModel):
    nodes: list[OrbitNode]


class ShoppingItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=10000)
    unit: str = Field(default="份", max_length=32)
    category: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=2000)


class ShoppingItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=1, le=10000)
    unit: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2000)
    checked: bool | None = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    due_at: str | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_at: str | None = None


class ReminderIn(BaseModel):
    task_id: str
    interval_minutes: int = Field(ge=1, le=10080)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    start_at: str | None = None
    missed_run_policy: str = Field(default="skip", pattern="^(skip|catch_up_bounded)$")
    catch_up_limit: int = Field(default=1, ge=1, le=10)


class MemoIn(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class ProposalIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    value_type: str = Field(default="string", pattern="^(string|number|boolean|json)$")
    category: str = Field(default="general", max_length=64)
    reason: str = Field(default="", max_length=500)


class PreferenceIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    value_type: str = Field(default="string", pattern="^(string|number|boolean|json)$")
    category: str = Field(default="general", max_length=64)


class AnomalyIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=2000)


def _shopping_view(item: ShoppingListItem) -> dict:
    return {"id": item.id, "name": item.name, "quantity": item.quantity, "unit": item.unit,
            "category": item.category, "note": item.note, "checked": item.checked,
            "created_at": item.created_at, "updated_at": item.updated_at}


def _low_stock_models(db: Session, household_id: str) -> list[dict]:
    rows = db.execute(select(Inventory.model_id, func.sum(Inventory.stock), Inventory.min_stock).where(
        Inventory.household_id == household_id).group_by(Inventory.model_id, Inventory.min_stock)).all()
    return [{"model_id": model_id, "stock": stock or 0} for model_id, stock, min_stock in rows
            if (stock or 0) <= (min_stock or 5)]


@router.get("/orbit", response_model=OrbitResponse)
def orbit(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    household_id = membership.household_id
    member_count = session.scalar(select(func.count()).select_from(HouseholdMember).where(
        HouseholdMember.household_id == household_id))
    setup = setup_status(session, account)
    tasks = HouseholdTaskService(session).today(household_id)
    shopping = ShoppingListService(session).summary(household_id)
    notifications = NotificationService(session).unread_count(household_id, account.id)
    anomalies = _low_stock_models(session, household_id)
    nodes = []
    if not setup.ready:
        nodes.append(OrbitNode(
            id="setup", label="完成模型初始化", kind="setup", status="attention", href="/admin/setup",
            detail="主聊天模型尚未配置",
        ))
    nodes.append(OrbitNode(
        id="household", label="家庭空间", kind="household", status="ready", href="/household",
        detail=f"{member_count} 位家庭成员",
    ))
    task_titles = "、".join(task.title for task in tasks[:3]) or "暂无今日任务"
    nodes.append(OrbitNode(
        id="tasks", label="今日任务", kind="tasks", status="attention" if tasks else "ready",
        href="/household", detail=task_titles,
    ))
    nodes.append(OrbitNode(
        id="shopping", label="购物清单", kind="shopping", status="attention" if shopping["unchecked"] else "ready",
        href="/household", detail=f"{shopping['unchecked']} 项待购买" + (
            f"（{'、'.join(item.name for item in ShoppingListService(session).list(household_id)[:3])}）"
            if shopping["unchecked"] else ""),
    ))
    nodes.append(OrbitNode(
        id="notifications", label="通知中心", kind="notifications",
        status="attention" if notifications else "ready", href="/household",
        detail=f"{notifications} 条未读通知",
    ))
    if anomalies:
        nodes.append(OrbitNode(
            id="device-anomalies", label="设备异常", kind="device", status="attention", href="/devices",
            detail="、".join(item["model_id"] for item in anomalies[:3]) + " 库存或状态异常",
        ))
    return OrbitResponse(nodes=nodes)


@router.get("/today")
def today(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    household_id = membership.household_id
    task_service = HouseholdTaskService(session)
    now = utcnow()
    tasks = task_service.today(household_id, now=now)
    schedules = ReminderScheduleService(session).list(household_id)
    upcoming = [schedule for schedule in schedules
                if schedule.active and schedule.next_run_at and schedule.next_run_at > now][:5]
    return {
        "date": now.date().isoformat(),
        "tasks": [{"id": task.id, "title": task.title, "description": task.description, "status": task.status,
                   "due_at": task.due_at} for task in tasks],
        "reminders": [{"id": schedule.id, "task_id": schedule.task_id, "interval_minutes": schedule.interval_minutes,
                       "next_run_at": schedule.next_run_at, "active": schedule.active} for schedule in upcoming],
        "shopping": ShoppingListService(session).summary(household_id),
        "notifications": {"unread": NotificationService(session).unread_count(household_id, account.id)},
        "anomalies": _low_stock_models(session, household_id),
    }


@router.get("/shopping-list")
def list_shopping(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    service = ShoppingListService(session)
    return {"items": [_shopping_view(item) for item in service.list(membership.household_id)],
            "summary": service.summary(membership.household_id)}


@router.post("/shopping-list", status_code=201)
def create_shopping(payload: ShoppingItemIn, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    item = ShoppingListService(session).upsert(membership.household_id, name=payload.name,
                                               quantity=payload.quantity, unit=payload.unit,
                                               category=payload.category, note=payload.note, account_id=account.id)
    session.commit()
    return _shopping_view(item)


@router.patch("/shopping-list/{item_id}")
def patch_shopping(item_id: str, payload: ShoppingItemPatch, account: Account = Depends(get_current_account),
                   session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    service = ShoppingListService(session)
    try:
        item = service.modify(membership.household_id, item_id, name=payload.name, quantity=payload.quantity,
                              unit=payload.unit, category=payload.category, note=payload.note, account_id=account.id)
        if payload.checked is not None:
            item = service.set_checked(membership.household_id, item_id, payload.checked, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return _shopping_view(item)


@router.delete("/shopping-list/{item_id}", status_code=204)
def delete_shopping(item_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        ShoppingListService(session).remove(membership.household_id, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return None


@router.get("/tasks")
def list_tasks(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    tasks = HouseholdTaskService(session).list(membership.household_id)
    return {"items": [{"id": task.id, "title": task.title, "description": task.description, "status": task.status,
                       "due_at": task.due_at, "completed_at": task.completed_at} for task in tasks]}


@router.post("/tasks", status_code=201)
def create_task(payload: TaskIn, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    task = HouseholdTaskService(session).create(membership.household_id, title=payload.title,
                                                description=payload.description,
                                                due_at=_parse_datetime(payload.due_at), account_id=account.id)
    session.commit()
    return {"id": task.id, "title": task.title, "description": task.description, "status": task.status,
            "due_at": task.due_at, "completed_at": task.completed_at}


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, payload: TaskPatch, account: Account = Depends(get_current_account),
               session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        task = HouseholdTaskService(session).update(membership.household_id, task_id, title=payload.title,
                                                    description=payload.description,
                                                    due_at=_parse_datetime(payload.due_at), account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": task.id, "title": task.title, "description": task.description, "status": task.status,
            "due_at": task.due_at, "completed_at": task.completed_at}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        task = HouseholdTaskService(session).complete(membership.household_id, task_id, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": task.id, "title": task.title, "status": task.status, "completed_at": task.completed_at}


@router.get("/reminders")
def list_reminders(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    schedules = ReminderScheduleService(session).list(membership.household_id)
    return {"items": [{"id": schedule.id, "task_id": schedule.task_id, "timezone": schedule.timezone,
                       "interval_minutes": schedule.interval_minutes, "next_run_at": schedule.next_run_at,
                       "missed_run_policy": schedule.missed_run_policy, "active": schedule.active}
                      for schedule in schedules]}


@router.post("/reminders", status_code=201)
def create_reminder(payload: ReminderIn, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        schedule = ReminderScheduleService(session).create(
            membership.household_id, task_id=payload.task_id, interval_minutes=payload.interval_minutes,
            timezone=payload.timezone, start_at=_parse_datetime(payload.start_at),
            missed_run_policy=payload.missed_run_policy, catch_up_limit=payload.catch_up_limit, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    session.commit()
    return {"id": schedule.id, "task_id": schedule.task_id, "timezone": schedule.timezone,
            "interval_minutes": schedule.interval_minutes, "next_run_at": schedule.next_run_at,
            "missed_run_policy": schedule.missed_run_policy, "active": schedule.active}


@router.delete("/reminders/{schedule_id}")
def cancel_reminder(schedule_id: str, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        schedule = ReminderScheduleService(session).cancel(membership.household_id, schedule_id,
                                                           account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": schedule.id, "active": schedule.active}


@router.get("/notifications")
def list_notifications(unread_only: bool = False, account: Account = Depends(get_current_account),
                       session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    items = NotificationService(session).list_for_account(membership.household_id, account.id,
                                                           unread_only=unread_only)
    return {"items": items, "unread": sum(1 for item in items if item["read_at"] is None)}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, account: Account = Depends(get_current_account),
                           session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        read = NotificationService(session).mark_read(membership.household_id, notification_id, account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": notification_id, "read_at": read.read_at}


@router.post("/device-anomalies", status_code=201)
def report_device_anomaly(payload: AnomalyIn, account: Account = Depends(get_current_account),
                          session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    device = session.scalar(select(Device).where(Device.household_id == membership.household_id,
                                                 Device.model_id == payload.model_id))
    if not device:
        raise HTTPException(status_code=404, detail={"code": "DEVICE_NOT_FOUND"})
    notification = NotificationService(session).create(
        membership.household_id, title=payload.title, body=payload.detail, kind="device_anomaly",
        source_type="device", source_id=device.id)
    session.commit()
    return {"id": notification.id, "kind": notification.kind, "title": notification.title}


@router.get("/preferences")
def list_preferences(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    items = PreferenceService(session).list(membership.household_id)
    return {"items": [{"key": item.key, "value": item.value, "category": item.category,
                       "value_type": item.value_type, "updated_at": item.updated_at} for item in items]}


@router.put("/preferences")
def set_preference(payload: PreferenceIn, account: Account = Depends(get_current_account),
                   session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        row = PreferenceService(session).set_confirmed(membership.household_id, key=payload.key, value=payload.value,
                                                       value_type=payload.value_type, category=payload.category,
                                                       account_id=account.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    session.commit()
    return {"key": row.key, "value": row.value, "category": row.category, "value_type": row.value_type}


@router.get("/preferences/proposals")
def list_proposals(status: str = "pending", account: Account = Depends(get_current_account),
                   session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    items = PreferenceService(session).proposals(membership.household_id, status=status)
    return {"items": [{"id": item.id, "key": item.key, "value": item.value, "category": item.category,
                       "value_type": item.value_type, "reason": item.reason, "status": item.status,
                       "proposed_by_kind": item.proposed_by_kind} for item in items]}


@router.post("/preferences/proposals", status_code=201)
def create_proposal(payload: ProposalIn, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    service = PreferenceService(session)
    try:
        proposal = service.propose(membership.household_id, key=payload.key, value=payload.value,
                                   value_type=payload.value_type, category=payload.category,
                                   reason=payload.reason, proposed_by_account_id=account.id,
                                   proposed_by_kind="member")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    session.commit()
    return {"id": proposal.id, "key": proposal.key, "value": proposal.value, "status": proposal.status}


@router.post("/preferences/proposals/{proposal_id}/confirm")
def confirm_proposal(proposal_id: str, account: Account = Depends(get_current_account),
                     session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        row = PreferenceService(session).confirm(membership.household_id, proposal_id, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"key": row.key, "value": row.value, "category": row.category, "value_type": row.value_type}


@router.post("/preferences/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        proposal = PreferenceService(session).reject(membership.household_id, proposal_id, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": proposal.id, "status": proposal.status}


@router.get("/memos")
def list_memos(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    items = HouseholdMemoService(session).list(membership.household_id)
    return {"items": [{"id": item.id, "content": item.content, "created_at": item.created_at,
                       "updated_at": item.updated_at} for item in items]}


@router.post("/memos", status_code=201)
def create_memo(payload: MemoIn, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    memo = HouseholdMemoService(session).create(membership.household_id, content=payload.content,
                                                account_id=account.id)
    session.commit()
    return {"id": memo.id, "content": memo.content, "created_at": memo.created_at, "updated_at": memo.updated_at}


@router.patch("/memos/{memo_id}")
def patch_memo(memo_id: str, payload: MemoIn, account: Account = Depends(get_current_account),
               session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        memo = HouseholdMemoService(session).update(membership.household_id, memo_id, content=payload.content,
                                                    account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": memo.id, "content": memo.content, "updated_at": memo.updated_at}


@router.delete("/memos/{memo_id}", status_code=204)
def delete_memo(memo_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        HouseholdMemoService(session).remove(membership.household_id, memo_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return None
