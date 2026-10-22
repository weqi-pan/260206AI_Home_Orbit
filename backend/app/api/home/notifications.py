from backend.app.api.home.base import *
from backend.app.api.home.base import _low_stock_models, _parse_datetime, _shopping_view
from backend.app.api.households import _membership

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
