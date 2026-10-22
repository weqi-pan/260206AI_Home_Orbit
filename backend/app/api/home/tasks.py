from backend.app.api.home.base import *
from backend.app.api.home.base import _low_stock_models, _parse_datetime, _shopping_view
from backend.app.api.households import _membership

@router.get("/tasks")
def list_tasks(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    tasks = HouseholdTaskService(session).list(membership.household_id)
    return {"items": [{"id": task.id, "title": task.title, "description": task.description, "status": task.status,
                       "due_at": task.due_at, "completed_at": task.completed_at} for task in tasks]}


@router.post("/tasks", status_code=201)
def create_task(payload: TaskIn, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    task = HouseholdTaskService(session).create(membership.household_id, title=payload.title,
                                                description=payload.description,
                                                due_at=_parse_datetime(payload.due_at), account_id=account.id)
    session.commit()
    return {"id": task.id, "title": task.title, "description": task.description, "status": task.status,
            "due_at": task.due_at, "completed_at": task.completed_at}


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, payload: TaskPatch, account: Account = Depends(get_current_account),
               session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        task = HouseholdTaskService(session).update(membership.household_id, task_id, title=payload.title,
                                                    description=payload.description,
                                                    due_at=_parse_datetime(payload.due_at), account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": task.id, "title": task.title, "description": task.description, "status": task.status,
            "due_at": task.due_at, "completed_at": task.completed_at}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        task = HouseholdTaskService(session).complete(membership.household_id, task_id, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": task.id, "title": task.title, "status": task.status, "completed_at": task.completed_at}
