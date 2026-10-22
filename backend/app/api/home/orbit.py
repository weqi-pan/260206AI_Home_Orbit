from backend.app.api.home.base import *
from backend.app.api.home.base import _low_stock_models, _parse_datetime, _shopping_view
from backend.app.api.households import _membership

@router.get("/orbit", response_model=OrbitResponse)
def orbit(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    household_id = membership.household_id
    member_count = session.scalar(select(func.count()).select_from(HouseholdMember).where(
        HouseholdMember.household_id == household_id))
    setup = setup_status(session, account)
    tasks = HouseholdTaskService(session).today(household_id)
    shopping = ShoppingListService(session).summary(household_id)
    notifications = NotificationService(session).unread_count(household_id, account.id)
    anomalies = _low_stock_models(session, household_id)
    nodes = []
    if not setup.ready:
        nodes.append(OrbitNode(
            id="setup", label="完成模型初始化", kind="setup", status="attention", href="/admin/setup",
            detail="主聊天模型尚未配置",
        ))
    nodes.append(OrbitNode(
        id="household", label="家庭空间", kind="household", status="ready", href="/household",
        detail=f"{member_count} 位家庭成员",
    ))
    task_titles = "、".join(task.title for task in tasks[:3]) or "暂无今日任务"
    nodes.append(OrbitNode(
        id="tasks", label="今日任务", kind="tasks", status="attention" if tasks else "ready",
        href="/household", detail=task_titles,
    ))
    nodes.append(OrbitNode(
        id="shopping", label="购物清单", kind="shopping", status="attention" if shopping["unchecked"] else "ready",
        href="/household", detail=f"{shopping['unchecked']} 项待购买" + (
            f"（{'、'.join(item.name for item in ShoppingListService(session).list(household_id)[:3])}）"
            if shopping["unchecked"] else ""),
    ))
    nodes.append(OrbitNode(
        id="notifications", label="通知中心", kind="notifications",
        status="attention" if notifications else "ready", href="/household",
        detail=f"{notifications} 条未读通知",
    ))
    if anomalies:
        nodes.append(OrbitNode(
            id="device-anomalies", label="设备异常", kind="device", status="attention", href="/devices",
            detail="、".join(item["model_id"] for item in anomalies[:3]) + " 库存或状态异常",
        ))
    return OrbitResponse(nodes=nodes)


@router.get("/today")
def today(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    household_id = membership.household_id
    task_service = HouseholdTaskService(session)
    now = utcnow()
    tasks = task_service.today(household_id, now=now)
    schedules = ReminderScheduleService(session).list(household_id)
    upcoming = [schedule for schedule in schedules
                if schedule.active and schedule.next_run_at and schedule.next_run_at > now][:5]
    return {
        "date": now.date().isoformat(),
        "tasks": [{"id": task.id, "title": task.title, "description": task.description, "status": task.status,
                   "due_at": task.due_at} for task in tasks],
        "reminders": [{"id": schedule.id, "task_id": schedule.task_id, "interval_minutes": schedule.interval_minutes,
                       "next_run_at": schedule.next_run_at, "active": schedule.active} for schedule in upcoming],
        "shopping": ShoppingListService(session).summary(household_id),
        "notifications": {"unread": NotificationService(session).unread_count(household_id, account.id)},
        "anomalies": _low_stock_models(session, household_id),
    }
