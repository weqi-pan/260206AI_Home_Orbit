from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.agent.tools.gateways import ToolRepositories
from backend.app.db.models import Account, HouseholdMember


router = APIRouter(prefix="/devices", tags=["devices"])


def household_id(db, account):
    return db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))


def repositories(request):
    return ToolRepositories(request.app.state.database.session_factory)


class CompareRequest(BaseModel):
    model_ids: list[str] = Field(min_length=2, max_length=4)


def device_dict(item, total_stock=0):
    return {"model_id": item.model_id, "model_name": item.model_name, "category": item.category, "specs": item.specs,
            "description": item.description, "price": item.price, "features": item.features, "total_stock": total_stock or 0}


@router.get("")
def search(request: Request, keyword: str = "", db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    home = household_id(db, account)
    repository = repositories(request)
    items = repository.search_devices(home, keyword)
    stocks = repository.total_stock(home, [item.model_id for item in items]) if items else {}
    return [device_dict(item, stocks.get(item.model_id, 0)) for item in items]


@router.post("/compare")
def compare(payload: CompareRequest, request: Request, db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    home = household_id(db, account)
    repository = repositories(request)
    found = {item.model_id: item for item in repository.devices(home, payload.model_ids)}
    if len(found) != len(set(payload.model_ids)):
        raise HTTPException(status_code=404, detail="设备不存在")
    stocks = repository.total_stock(home, payload.model_ids)
    return [device_dict(found[item_id], stocks.get(item_id, 0)) for item_id in payload.model_ids]


@router.post("/restock-notify/{model_id}")
def restock(model_id: str, request: Request, db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    home = household_id(db, account)
    repository = repositories(request)
    if not repository.device(home, model_id):
        raise HTTPException(status_code=404, detail="设备不存在")
    created = repository.restock_notification(home, account.id, model_id) is None
    item = repository.register_restock(home, account.id, model_id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content={"id": item.id, "account_id": item.account_id, "model_id": item.model_id, "status": item.status},
    )


@router.get("/{model_id}")
def detail(model_id: str, request: Request, db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    home = household_id(db, account)
    repository = repositories(request)
    item = repository.device(home, model_id)
    if not item:
        raise HTTPException(status_code=404, detail="设备不存在")
    stock = repository.total_stock(home, [model_id]).get(model_id, 0)
    return device_dict(item, stock)
