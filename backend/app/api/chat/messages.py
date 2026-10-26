from backend.app.api.chat.base import *

@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    owned_session(db, session_id, account.id)
    items = db.scalars(select(ConversationHistory).where(ConversationHistory.session_id == session_id).order_by(ConversationHistory.id)).all()
    return [{"id": item.id, "role": item.role, "content": item.content, "sentiment": item.sentiment,
             "sentiment_score": item.sentiment_score, "feedback": item.feedback, "created_at": item.created_at} for item in items]


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: int, payload: FeedbackRequest, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    item = db.scalar(select(ConversationHistory).join(ChatSession).where(
        ConversationHistory.id == message_id, ChatSession.account_id == account.id
    ))
    if not item:
        raise HTTPException(status_code=404, detail={"code": "MESSAGE_NOT_FOUND", "message": "消息不存在"})
    if item.role != "assistant":
        raise HTTPException(status_code=400, detail={"code": "FEEDBACK_ASSISTANT_ONLY", "message": "仅支持评价助手消息"})
    item.feedback = payload.feedback
    db.commit()
    return {"id": item.id, "feedback": item.feedback}
