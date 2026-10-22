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


def _audit(db, admin, action, resource_type, resource_id, detail, household_id=None):
    from backend.app.audit.service import audit_admin_action

    return audit_admin_action(db, admin, action, resource_type, resource_id, detail, household_id=household_id)


def device_dict(item):
    return {"model_id": item.model_id, "model_name": item.model_name, "category": item.category, "specs": item.specs,
            "description": item.description, "price": item.price, "features": item.features}


def ticket_dict(item):
    return {"id": item.id, "household_id": item.household_id, "account_id": item.account_id,
            "session_id": item.session_id, "title": item.title, "summary": item.summary,
            "emotion": item.emotion, "priority": item.priority, "status": item.status, "admin_reply": item.admin_reply,
            "created_at": item.created_at, "resolved_at": item.resolved_at}
