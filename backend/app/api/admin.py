from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from backend.app.core.security import get_db, hash_password, require_admin
from backend.app.db.models import (Account, AdminActionAudit, ChatSession, ConversationHistory, Device, FaqPair, Household,
                                   HouseholdMember, Inventory, KnowledgeFile, RestockNotification, Ticket, User, utcnow)


router = APIRouter(prefix="/admin", tags=["admin"])


class AccountUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    role: str | None = Field(default=None, pattern="^(admin|user)$")


class ResetPassword(BaseModel):
    password: str = Field(min_length=8, max_length=72)


class DevicePayload(BaseModel):
    model_id: str
    model_name: str
    category: str
    specs: str
    description: str
    price: float = Field(ge=0)
    features: str


class InventoryPayload(BaseModel):
    model_id: str
    warehouse: str
    stock: int = Field(ge=0)
    min_stock: int = Field(ge=0)


class NotificationUpdate(BaseModel):
    status: str = Field(pattern="^(waiting|notified)$")


class TicketUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|processing|resolved|closed)$")
    priority: str | None = Field(default=None, pattern="^(low|normal|high)$")
    admin_reply: str | None = None


def account_dict(item):
    return {"id": item.id, "username": item.username, "role": item.role, "status": item.status, "user_id": item.user_id,
            "must_change_password": item.must_change_password, "created_at": item.created_at}


def device_dict(item):
    return {"model_id": item.model_id, "model_name": item.model_name, "category": item.category, "specs": item.specs,
            "description": item.description, "price": item.price, "features": item.features}


def ticket_dict(item):
    return {"id": item.id, "household_id": item.household_id, "account_id": item.account_id,
            "session_id": item.session_id, "title": item.title, "summary": item.summary,
            "emotion": item.emotion, "priority": item.priority, "status": item.status, "admin_reply": item.admin_reply,
            "created_at": item.created_at, "resolved_at": item.resolved_at}


@router.get("/dashboard")
def dashboard(_admin: Account = Depends(require_admin), session: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    likes, dislikes = session.execute(select(
        func.sum(case((ConversationHistory.feedback == "like", 1), else_=0)),
        func.sum(case((ConversationHistory.feedback == "dislike", 1), else_=0)),
    ).where(ConversationHistory.role == "assistant")).one()
    low_rows = session.execute(select(Device.household_id, Device.model_id, Device.model_name,
        func.sum(Inventory.stock), func.max(Inventory.min_stock)).join(Inventory).where(
        Inventory.household_id == Device.household_id).group_by(Device.household_id, Device.model_id, Device.model_name)
                               .having(func.sum(Inventory.stock) <= func.max(Inventory.min_stock))).all()
    ticket_counts = dict(session.execute(select(Ticket.status, func.count()).group_by(Ticket.status)).all())
    return {
        "accounts": session.scalar(select(func.count()).select_from(Account)) or 0,
        "users": session.scalar(select(func.count()).select_from(User)) or 0,
        "devices": session.scalar(select(func.count()).select_from(Device)) or 0,
        "open_tickets": ticket_counts.get("open", 0),
        "today_sessions": session.scalar(select(func.count()).select_from(ChatSession).where(ChatSession.created_at >= today)) or 0,
        "messages_last_7_days": session.scalar(select(func.count()).select_from(ConversationHistory).where(ConversationHistory.created_at >= week)) or 0,
        "satisfaction_rate": (likes or 0) / ((likes or 0) + (dislikes or 0)) if (likes or 0) + (dislikes or 0) else 0,
        "ticket_status_counts": ticket_counts,
        "low_stock_devices": [{"household_id": row[0], "model_id": row[1], "model_name": row[2],
                               "total_stock": row[3], "stock_threshold": row[4]} for row in low_rows],
        "knowledge_files": session.scalar(select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.status == "active")) or 0,
        "approved_faq": session.scalar(select(func.count()).select_from(FaqPair).where(FaqPair.status == "approved")) or 0,
    }


@router.get("/accounts")
def accounts(_admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    return [account_dict(item) for item in db.scalars(select(Account).order_by(Account.created_at)).all()]


@router.put("/accounts/{account_id}")
def update_account(account_id: str, payload: AccountUpdate, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Account, account_id)
    if not item: raise HTTPException(status_code=404, detail="账号不存在")
    disabling_admin = item.role == "admin" and item.status == "active" and (
        payload.status == "disabled" or payload.role == "user"
    )
    if item.id == admin.id and disabling_admin:
        raise HTTPException(status_code=400, detail="不能禁用或降级当前管理员账号")
    if disabling_admin:
        active_admins = db.scalar(select(func.count()).select_from(Account).where(
            Account.role == "admin", Account.status == "active"
        )) or 0
        if active_admins <= 1:
            raise HTTPException(status_code=400, detail="至少保留一个启用的管理员账号")
    if payload.status is not None: item.status = payload.status
    if payload.role is not None: item.role = payload.role
    db.commit(); db.refresh(item)
    return account_dict(item)


@router.post("/accounts/{account_id}/reset-password", status_code=204)
def reset_password(account_id: str, payload: ResetPassword, request: Request, response: Response,
                   _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Account, account_id)
    if not item: raise HTTPException(status_code=404, detail="账号不存在")
    item.password_hash = hash_password(payload.password, request.app.state.settings.bcrypt_rounds)
    item.must_change_password = True
    db.commit(); response.status_code = 204


@router.get("/households")
def households(_admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(Household).order_by(Household.created_at)).all()
    members = db.execute(select(HouseholdMember.household_id, HouseholdMember.account_id,
                                 HouseholdMember.role, Account.username).join(
        Account, Account.id == HouseholdMember.account_id)).all()
    grouped: dict[str, list] = {}
    for household_id, account_id, role, username in members:
        grouped.setdefault(household_id, []).append(
            {"account_id": account_id, "username": username, "role": role})
    return [{"id": row.id, "name": row.name, "created_at": row.created_at,
             "members": grouped.get(row.id, [])} for row in rows]


@router.get("/devices")
def devices(household_id: str | None = None, _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    query = select(Device)
    if household_id:
        query = query.where(Device.household_id == household_id)
    return [{**device_dict(item), "household_id": item.household_id} for item in db.scalars(query).all()]


@router.post("/devices", status_code=201)
def create_device(payload: DevicePayload, household_id: str, _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == payload.model_id)):
        raise HTTPException(status_code=409, detail="设备已存在")
    item = Device(household_id=household_id, **payload.model_dump()); db.add(item); db.commit(); return device_dict(item)


@router.put("/devices/{model_id}")
def update_device(model_id: str, payload: DevicePayload, household_id: str,
                  _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))
    if not item: raise HTTPException(status_code=404, detail="设备不存在")
    for key, value in payload.model_dump(exclude={"model_id"}).items(): setattr(item, key, value)
    db.commit(); return device_dict(item)


@router.delete("/devices/{model_id}", status_code=204)
def delete_device(model_id: str, response: Response, household_id: str,
                  _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))
    if not item: raise HTTPException(status_code=404, detail="设备不存在")
    db.execute(delete(RestockNotification).where(RestockNotification.household_id == household_id,
                                                  RestockNotification.model_id == model_id))
    db.execute(delete(Inventory).where(Inventory.household_id == household_id, Inventory.model_id == model_id))
    db.delete(item); db.commit(); response.status_code = 204


@router.get("/inventory")
def list_inventory(household_id: str | None = None, model_id: str | None = None,
                   _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    query = select(Inventory)
    if household_id:
        query = query.where(Inventory.household_id == household_id)
    if model_id:
        query = query.where(Inventory.model_id == model_id)
    return [{"household_id": item.household_id, "model_id": item.model_id, "warehouse": item.warehouse,
             "stock": item.stock, "min_stock": item.min_stock, "updated_at": item.updated_at}
            for item in db.scalars(query.order_by(Inventory.model_id, Inventory.warehouse)).all()]


@router.put("/inventory")
def inventory(payload: InventoryPayload, household_id: str,
              _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    device = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == payload.model_id))
    if not device: raise HTTPException(status_code=404, detail="设备不存在")
    item = db.scalar(select(Inventory).where(Inventory.household_id == household_id,
        Inventory.model_id == payload.model_id, Inventory.warehouse == payload.warehouse))
    if not item: item = Inventory(household_id=household_id, device_id=device.id,
                                  model_id=payload.model_id, warehouse=payload.warehouse); db.add(item)
    item.stock = payload.stock; item.min_stock = payload.min_stock; db.commit(); db.refresh(item)
    return {"id": item.id, "model_id": item.model_id, "warehouse": item.warehouse, "stock": item.stock, "min_stock": item.min_stock}


@router.get("/restock-notifications")
def notifications(_admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": item.id, "household_id": item.household_id, "account_id": item.account_id,
             "model_id": item.model_id, "status": item.status, "created_at": item.created_at}
            for item in db.scalars(select(RestockNotification).order_by(RestockNotification.id.desc())).all()]


@router.put("/restock-notifications/{notification_id}")
def update_notification(notification_id: int, payload: NotificationUpdate, household_id: str,
                        _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(RestockNotification).where(RestockNotification.id == notification_id,
                                                        RestockNotification.household_id == household_id))
    if not item: raise HTTPException(status_code=404, detail="通知不存在")
    item.status = payload.status; db.commit()
    return {"id": item.id, "account_id": item.account_id, "model_id": item.model_id, "status": item.status}


@router.get("/tickets")
def tickets(household_id: str | None = None, _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    query = select(Ticket).order_by(Ticket.id.desc())
    if household_id: query = query.where(Ticket.household_id == household_id)
    return [ticket_dict(item) for item in db.scalars(query).all()]


@router.put("/households/{household_id}/tickets/{ticket_id}")
def update_ticket(household_id: str, ticket_id: int, payload: TicketUpdate,
                  admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.household_id == household_id))
    if not item: raise HTTPException(status_code=404, detail="工单不存在")
    if payload.status is not None:
        item.status = payload.status
        item.resolved_at = utcnow() if payload.status in {"resolved", "closed"} else None
    if payload.priority is not None: item.priority = payload.priority
    if payload.admin_reply is not None: item.admin_reply = payload.admin_reply
    db.add(AdminActionAudit(principal_id=admin.id, household_id=household_id, action="ticket.update",
        resource_type="ticket", resource_id=str(item.id), detail_json=json.dumps(payload.model_dump(exclude_none=True),
                                                                                 ensure_ascii=False, sort_keys=True)))
    db.commit(); return ticket_dict(item)


class TaskRunRequest(BaseModel):
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=200)
    household_id: str | None = Field(default=None, max_length=36)


@router.get("/tasks")
def list_celery_tasks(task_type: str | None = None, limit: int = 50,
                      _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    from backend.app.db.models import CeleryTaskRecord

    query = select(CeleryTaskRecord).order_by(CeleryTaskRecord.created_at.desc()).limit(min(limit, 200))
    if task_type:
        query = query.where(CeleryTaskRecord.task_type == task_type)
    return {"items": [{"id": row.id, "idempotency_key": row.idempotency_key, "task_type": row.task_type,
                       "household_id": row.household_id, "status": row.status, "attempt": row.attempt,
                       "max_attempts": row.max_attempts, "error_code": row.error_code,
                       "next_retry_at": row.next_retry_at, "created_at": row.created_at,
                       "updated_at": row.updated_at} for row in db.scalars(query).all()]}


@router.post("/tasks/{task_type}/run")
def run_celery_task(task_type: str, payload: TaskRunRequest, request: Request,
                    admin: Account = Depends(require_admin)):
    from backend.app.tasks.executor import PermanentTaskError
    from backend.app.tasks.manual import build_executor

    executor = build_executor(request.app.state.database.session_factory)
    key = payload.idempotency_key or f"admin:{task_type}:{payload.household_id or 'global'}"
    executor.submit(task_type, payload.payload, key, household_id=payload.household_id)
    try:
        record = executor.run(key)
    except PermanentTaskError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
    db = request.app.state.database.session_factory()
    try:
        db.add(AdminActionAudit(principal_id=admin.id, household_id=payload.household_id,
                                action="celery_task.run", resource_type="celery_task", resource_id=key,
                                detail_json=json.dumps({"task_type": task_type}, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    return {"idempotency_key": key, "status": record.status, "attempt": record.attempt,
            "error_code": record.error_code}
