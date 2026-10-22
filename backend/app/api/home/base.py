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
