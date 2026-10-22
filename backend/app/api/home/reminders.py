from backend.app.api.home.base import *
from backend.app.api.home.base import _low_stock_models, _parse_datetime, _shopping_view
from backend.app.api.households import _membership

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
