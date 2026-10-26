from backend.app.api.admin.base import *
from backend.app.api.admin.base import _audit, device_dict

@router.get("/devices")
def devices(household_id: str | None = None, _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    query = select(Device)
    if household_id:
        query = query.where(Device.household_id == household_id)
    return [{**device_dict(item), "household_id": item.household_id} for item in db.scalars(query).all()]


@router.post("/devices", status_code=201)
def create_device(payload: DevicePayload, household_id: str, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == payload.model_id)):
        raise HTTPException(status_code=409, detail={"code": "DEVICE_EXISTS", "message": "设备已存在"})
    item = Device(household_id=household_id, **payload.model_dump()); db.add(item); db.commit()
    _audit(db, admin, "admin.device.create", "device", payload.model_id,
           {"model_name": payload.model_name, "category": payload.category}, household_id=household_id)
    return device_dict(item)


@router.put("/devices/{model_id}")
def update_device(model_id: str, payload: DevicePayload, household_id: str,
                  admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))
    if not item: raise HTTPException(status_code=404, detail={"code": "DEVICE_NOT_FOUND", "message": "设备不存在"})
    for key, value in payload.model_dump(exclude={"model_id"}).items(): setattr(item, key, value)
    db.commit()
    _audit(db, admin, "admin.device.update", "device", model_id,
           payload.model_dump(exclude={"specs", "features"}), household_id=household_id)
    return device_dict(item)


@router.delete("/devices/{model_id}", status_code=204)
def delete_device(model_id: str, response: Response, household_id: str,
                  admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))
    if not item: raise HTTPException(status_code=404, detail={"code": "DEVICE_NOT_FOUND", "message": "设备不存在"})
    db.execute(delete(RestockNotification).where(RestockNotification.household_id == household_id,
                                                  RestockNotification.model_id == model_id))
    db.execute(delete(Inventory).where(Inventory.household_id == household_id, Inventory.model_id == model_id))
    db.delete(item); db.commit(); response.status_code = 204
    _audit(db, admin, "admin.device.delete", "device", model_id, {}, household_id=household_id)
