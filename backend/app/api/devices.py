from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, Device, Inventory, RestockNotification


router = APIRouter(prefix="/devices", tags=["devices"])


class CompareRequest(BaseModel):
    model_ids: list[str] = Field(min_length=2, max_length=4)


def device_dict(item, total_stock=0):
    return {"model_id": item.model_id, "model_name": item.model_name, "category": item.category, "specs": item.specs,
            "description": item.description, "price": item.price, "features": item.features, "total_stock": total_stock or 0}


def stock_map(db, ids):
    return dict(db.execute(select(Inventory.model_id, func.sum(Inventory.stock)).where(Inventory.model_id.in_(ids)).group_by(Inventory.model_id)).all())


@router.get("")
def search(keyword: str = "", db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    query = select(Device)
    if keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.where(or_(Device.model_id.like(pattern), Device.model_name.like(pattern), Device.category.like(pattern), Device.description.like(pattern), Device.features.like(pattern)))
    items = db.scalars(query.order_by(Device.price)).all()
    stocks = stock_map(db, [item.model_id for item in items]) if items else {}
    return [device_dict(item, stocks.get(item.model_id, 0)) for item in items]


@router.post("/compare")
def compare(payload: CompareRequest, db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    found = {item.model_id: item for item in db.scalars(select(Device).where(Device.model_id.in_(payload.model_ids))).all()}
    if len(found) != len(set(payload.model_ids)):
        raise HTTPException(status_code=404, detail="设备不存在")
    stocks = stock_map(db, payload.model_ids)
    return [device_dict(found[item_id], stocks.get(item_id, 0)) for item_id in payload.model_ids]


@router.post("/restock-notify/{model_id}")
def restock(model_id: str, db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    if not db.get(Device, model_id):
        raise HTTPException(status_code=404, detail="设备不存在")
    item = db.scalar(select(RestockNotification).where(RestockNotification.account_id == account.id, RestockNotification.model_id == model_id))
    created = item is None
    if not item:
        item = RestockNotification(account_id=account.id, model_id=model_id)
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            item = db.scalar(select(RestockNotification).where(RestockNotification.account_id == account.id, RestockNotification.model_id == model_id))
        else:
            db.refresh(item)
    return __import__("fastapi").responses.JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content={"id": item.id, "account_id": item.account_id, "model_id": item.model_id, "status": item.status},
    )


@router.get("/{model_id}")
def detail(model_id: str, db: Session = Depends(get_db), account: Account = Depends(get_current_account)):
    item = db.get(Device, model_id)
    if not item:
        raise HTTPException(status_code=404, detail="设备不存在")
    return device_dict(item, stock_map(db, [model_id]).get(model_id, 0))
