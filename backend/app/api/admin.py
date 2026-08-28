from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from backend.app.core.security import get_db, hash_password, require_admin
from backend.app.db.models import Account, ChatSession, ConversationHistory, Device, FaqPair, HouseholdMember, Inventory, KnowledgeFile, RestockNotification, Ticket, User, utcnow


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def admin_household(db):
    admin = db.scalar(select(Account).where(Account.role == "admin"))
    return db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))


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
    return {"id": item.id, "account_id": item.account_id, "session_id": item.session_id, "title": item.title, "summary": item.summary,
            "emotion": item.emotion, "priority": item.priority, "status": item.status, "admin_reply": item.admin_reply,
            "created_at": item.created_at, "resolved_at": item.resolved_at}


@router.get("/dashboard")
def dashboard(session: Session = Depends(get_db)):
    household_id = admin_household(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    likes, dislikes = session.execute(select(
        func.sum(case((ConversationHistory.feedback == "like", 1), else_=0)),
        func.sum(case((ConversationHistory.feedback == "dislike", 1), else_=0)),
    ).where(ConversationHistory.role == "assistant")).one()
    low_rows = session.execute(select(Device.model_id, Device.model_name, func.sum(Inventory.stock), func.max(Inventory.min_stock))
                               .join(Inventory).where(Device.household_id == household_id,
                                                      Inventory.household_id == household_id).group_by(Device.model_id, Device.model_name)
                               .having(func.sum(Inventory.stock) <= func.max(Inventory.min_stock))).all()
    ticket_counts = dict(session.execute(select(Ticket.status, func.count()).where(
        Ticket.household_id == household_id).group_by(Ticket.status)).all())
    return {
        "accounts": session.scalar(select(func.count()).select_from(Account)) or 0,
        "users": session.scalar(select(func.count()).select_from(User)) or 0,
        "devices": session.scalar(select(func.count()).select_from(Device).where(Device.household_id == household_id)) or 0,
        "open_tickets": ticket_counts.get("open", 0),
        "today_sessions": session.scalar(select(func.count()).select_from(ChatSession).where(ChatSession.created_at >= today)) or 0,
        "messages_last_7_days": session.scalar(select(func.count()).select_from(ConversationHistory).where(ConversationHistory.created_at >= week)) or 0,
        "satisfaction_rate": (likes or 0) / ((likes or 0) + (dislikes or 0)) if (likes or 0) + (dislikes or 0) else 0,
        "ticket_status_counts": ticket_counts,
        "low_stock_devices": [{"model_id": row[0], "model_name": row[1], "total_stock": row[2], "stock_threshold": row[3]} for row in low_rows],
        "knowledge_files": session.scalar(select(func.count()).select_from(KnowledgeFile).where(
            KnowledgeFile.household_id == household_id, KnowledgeFile.status == "active")) or 0,
        "approved_faq": session.scalar(select(func.count()).select_from(FaqPair).where(
            FaqPair.household_id == household_id, FaqPair.status == "approved")) or 0,
    }


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)):
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
def reset_password(account_id: str, payload: ResetPassword, request: Request, response: Response, db: Session = Depends(get_db)):
    item = db.get(Account, account_id)
    if not item: raise HTTPException(status_code=404, detail="账号不存在")
    item.password_hash = hash_password(payload.password, request.app.state.settings.bcrypt_rounds)
    item.must_change_password = True
    db.commit(); response.status_code = 204


@router.get("/devices")
def devices(db: Session = Depends(get_db)):
    return [device_dict(item) for item in db.scalars(select(Device).where(Device.household_id == admin_household(db))).all()]


@router.post("/devices", status_code=201)
def create_device(payload: DevicePayload, db: Session = Depends(get_db)):
    household_id = admin_household(db)
    if db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == payload.model_id)):
        raise HTTPException(status_code=409, detail="设备已存在")
    item = Device(household_id=household_id, **payload.model_dump()); db.add(item); db.commit(); return device_dict(item)


@router.put("/devices/{model_id}")
def update_device(model_id: str, payload: DevicePayload, db: Session = Depends(get_db)):
    item = db.scalar(select(Device).where(Device.household_id == admin_household(db), Device.model_id == model_id))
    if not item: raise HTTPException(status_code=404, detail="设备不存在")
    for key, value in payload.model_dump(exclude={"model_id"}).items(): setattr(item, key, value)
    db.commit(); return device_dict(item)


@router.delete("/devices/{model_id}", status_code=204)
def delete_device(model_id: str, response: Response, db: Session = Depends(get_db)):
    household_id = admin_household(db)
    item = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))
    if not item: raise HTTPException(status_code=404, detail="设备不存在")
    db.execute(delete(RestockNotification).where(RestockNotification.household_id == household_id,
                                                  RestockNotification.model_id == model_id))
    db.execute(delete(Inventory).where(Inventory.household_id == household_id, Inventory.model_id == model_id))
    db.delete(item); db.commit(); response.status_code = 204


@router.get("/inventory")
def list_inventory(model_id: str | None = None, db: Session = Depends(get_db)):
    query = select(Inventory).where(Inventory.household_id == admin_household(db)).order_by(Inventory.model_id, Inventory.warehouse)
    if model_id:
        query = query.where(Inventory.model_id == model_id)
    return [{"model_id": item.model_id, "warehouse": item.warehouse, "stock": item.stock,
             "min_stock": item.min_stock, "updated_at": item.updated_at}
            for item in db.scalars(query).all()]


@router.put("/inventory")
def inventory(payload: InventoryPayload, db: Session = Depends(get_db)):
    household_id = admin_household(db)
    device = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == payload.model_id))
    if not device: raise HTTPException(status_code=404, detail="设备不存在")
    item = db.scalar(select(Inventory).where(Inventory.household_id == household_id,
        Inventory.model_id == payload.model_id, Inventory.warehouse == payload.warehouse))
    if not item: item = Inventory(household_id=household_id, device_id=device.id,
                                  model_id=payload.model_id, warehouse=payload.warehouse); db.add(item)
    item.stock = payload.stock; item.min_stock = payload.min_stock; db.commit(); db.refresh(item)
    return {"id": item.id, "model_id": item.model_id, "warehouse": item.warehouse, "stock": item.stock, "min_stock": item.min_stock}


@router.get("/restock-notifications")
def notifications(db: Session = Depends(get_db)):
    return [{"id": item.id, "account_id": item.account_id, "model_id": item.model_id, "status": item.status, "created_at": item.created_at}
            for item in db.scalars(select(RestockNotification).where(
                RestockNotification.household_id == admin_household(db)).order_by(RestockNotification.id.desc())).all()]


@router.put("/restock-notifications/{notification_id}")
def update_notification(notification_id: int, payload: NotificationUpdate, db: Session = Depends(get_db)):
    item = db.scalar(select(RestockNotification).where(RestockNotification.id == notification_id,
                                                        RestockNotification.household_id == admin_household(db)))
    if not item: raise HTTPException(status_code=404, detail="通知不存在")
    item.status = payload.status; db.commit()
    return {"id": item.id, "account_id": item.account_id, "model_id": item.model_id, "status": item.status}


@router.get("/tickets")
def tickets(db: Session = Depends(get_db)): return [ticket_dict(item) for item in db.scalars(select(Ticket).where(
    Ticket.household_id == admin_household(db)).order_by(Ticket.id.desc())).all()]


@router.put("/tickets/{ticket_id}")
def update_ticket(ticket_id: int, payload: TicketUpdate, db: Session = Depends(get_db)):
    item = db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.household_id == admin_household(db)))
    if not item: raise HTTPException(status_code=404, detail="工单不存在")
    if payload.status is not None:
        item.status = payload.status
        item.resolved_at = utcnow() if payload.status in {"resolved", "closed"} else None
    if payload.priority is not None: item.priority = payload.priority
    if payload.admin_reply is not None: item.admin_reply = payload.admin_reply
    db.commit(); return ticket_dict(item)
