from backend.app.api.admin.base import *

@router.get("/dashboard")
def dashboard(_admin: Account = Depends(require_admin), session: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    likes, dislikes = session.execute(select(
        func.sum(case((ConversationHistory.feedback == "like", 1), else_=0)),
        func.sum(case((ConversationHistory.feedback == "dislike", 1), else_=0)),
    ).where(ConversationHistory.role == "assistant")).one()
    low_rows = session.execute(select(Device.household_id, Device.model_id, Device.model_name,
        func.sum(Inventory.stock), func.max(Inventory.min_stock)).join(Inventory).where(
        Inventory.household_id == Device.household_id).group_by(Device.household_id, Device.model_id, Device.model_name)
                               .having(func.sum(Inventory.stock) <= func.max(Inventory.min_stock))).all()
    ticket_counts = dict(session.execute(select(Ticket.status, func.count()).group_by(Ticket.status)).all())
    return {
        "accounts": session.scalar(select(func.count()).select_from(Account)) or 0,
        "users": session.scalar(select(func.count()).select_from(User)) or 0,
        "devices": session.scalar(select(func.count()).select_from(Device)) or 0,
        "open_tickets": ticket_counts.get("open", 0),
        "today_sessions": session.scalar(select(func.count()).select_from(ChatSession).where(ChatSession.created_at >= today)) or 0,
        "messages_last_7_days": session.scalar(select(func.count()).select_from(ConversationHistory).where(ConversationHistory.created_at >= week)) or 0,
        "satisfaction_rate": (likes or 0) / ((likes or 0) + (dislikes or 0)) if (likes or 0) + (dislikes or 0) else 0,
        "ticket_status_counts": ticket_counts,
        "low_stock_devices": [{"household_id": row[0], "model_id": row[1], "model_name": row[2],
                               "total_stock": row[3], "stock_threshold": row[4]} for row in low_rows],
        "knowledge_files": session.scalar(select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.status == "active")) or 0,
        "approved_faq": session.scalar(select(func.count()).select_from(FaqPair).where(FaqPair.status == "approved")) or 0,
    }
