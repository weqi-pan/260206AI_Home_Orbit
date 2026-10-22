from backend.app.api.admin.base import *
from backend.app.api.admin.base import _audit, ticket_dict

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
             admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    device = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == payload.model_id))
    if not device: raise HTTPException(status_code=404, detail="设备不存在")
    item = db.scalar(select(Inventory).where(Inventory.household_id == household_id,
        Inventory.model_id == payload.model_id, Inventory.warehouse == payload.warehouse))
    if not item: item = Inventory(household_id=household_id, device_id=device.id,
                                  model_id=payload.model_id, warehouse=payload.warehouse); db.add(item)
    item.stock = payload.stock; item.min_stock = payload.min_stock; db.commit(); db.refresh(item)
    _audit(db, admin, "admin.inventory.upsert", "inventory", payload.model_id,
           payload.model_dump(), household_id=household_id)
    return {"id": item.id, "model_id": item.model_id, "warehouse": item.warehouse, "stock": item.stock, "min_stock": item.min_stock}


@router.get("/restock-notifications")
def notifications(_admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": item.id, "household_id": item.household_id, "account_id": item.account_id,
             "model_id": item.model_id, "status": item.status, "created_at": item.created_at}
            for item in db.scalars(select(RestockNotification).order_by(RestockNotification.id.desc())).all()]


@router.put("/restock-notifications/{notification_id}")
def update_notification(notification_id: int, payload: NotificationUpdate, household_id: str,
                        admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.scalar(select(RestockNotification).where(RestockNotification.id == notification_id,
                                                        RestockNotification.household_id == household_id))
    if not item: raise HTTPException(status_code=404, detail="通知不存在")
    item.status = payload.status; db.commit()
    _audit(db, admin, "admin.restock_notification.update", "restock_notification", item.id,
           {"status": payload.status}, household_id=household_id)
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
