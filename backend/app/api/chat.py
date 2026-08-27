import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, ChatSession, ConversationHistory, Ticket
from backend.app.services.defaults import get_agent_runner, get_followup_service, get_sentiment_service, get_title_service


router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)
AUX_EXECUTOR = ThreadPoolExecutor(max_workers=8)
TITLE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat-title")
SENTIMENT_DEADLINE_SECONDS = 3.0
AUX_TIMEOUT_SECONDS = 0.25

_pending_title_futures: set[Future] = set()


def pending_title_futures() -> set[Future]:
    return set(_pending_title_futures)


def _update_session_title(app, session_id: str, user_text: str, answer: str, title_service) -> None:
    try:
        title = title_service.generate(user_text, answer)
        if title:
            with app.state.database.session_factory.begin() as title_db:
                item = title_db.get(ChatSession, session_id)
                if item:
                    item.title = title[:100]
    except Exception:
        logger.exception("会话标题生成失败 session_id=%s", session_id)


def submit_title_generation(app, session_id: str, user_text: str, answer: str) -> Future | None:
    title_service = getattr(app.state, "title_service", None) or get_title_service()
    future = TITLE_EXECUTOR.submit(_update_session_title, app, session_id, user_text, answer, title_service)
    _pending_title_futures.add(future)
    future.add_done_callback(_pending_title_futures.discard)
    return future


class SessionCreate(BaseModel):
    title: str = Field(default="新对话", max_length=100)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class FeedbackRequest(BaseModel):
    feedback: str = Field(pattern="^(like|dislike)$")


def session_dict(item):
    return {"id": item.id, "title": item.title, "created_at": item.created_at, "updated_at": item.updated_at}


def owned_session(db, session_id, account_id):
    item = db.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.account_id == account_id))
    if not item:
        raise HTTPException(status_code=404, detail="会话不存在")
    return item


@router.get("/sessions")
def list_sessions(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    items = db.scalars(select(ChatSession).where(ChatSession.account_id == account.id).order_by(ChatSession.updated_at.desc())).all()
    return [session_dict(item) for item in items]


@router.post("/sessions", status_code=201)
def create_session(payload: SessionCreate, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    item = ChatSession(account_id=account.id, title=payload.title)
    db.add(item)
    db.commit()
    db.refresh(item)
    return session_dict(item)


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, payload: SessionUpdate, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    item = owned_session(db, session_id, account.id)
    item.title = payload.title
    db.commit()
    db.refresh(item)
    return session_dict(item)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, response: Response, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    item = owned_session(db, session_id, account.id)
    db.execute(update(Ticket).where(Ticket.session_id == item.id).values(session_id=None))
    db.execute(delete(ConversationHistory).where(ConversationHistory.session_id == item.id))
    db.delete(item)
    db.commit()
    response.status_code = 204


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
        raise HTTPException(status_code=404, detail="消息不存在")
    if item.role != "assistant":
        raise HTTPException(status_code=400, detail="仅支持评价助手消息")
    item.feedback = payload.feedback
    db.commit()
    return {"id": item.id, "feedback": item.feedback}


@router.post("/sessions/{session_id}/chat")
def chat(session_id: str, payload: ChatRequest, request: Request, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    chat_session = owned_session(db, session_id, account.id)
    previous_count = db.scalar(select(ConversationHistory.id).where(ConversationHistory.session_id == session_id).limit(1))
    user_message = ConversationHistory(session_id=session_id, user_id=account.user_id, role="user", content=payload.message)
    db.add(user_message)
    db.commit()
    history = db.scalars(select(ConversationHistory).where(ConversationHistory.session_id == session_id).order_by(ConversationHistory.id)).all()
    messages = [{"role": item.role, "content": item.content} for item in history]
    context = {"account_id": account.id, "user_id": account.user_id, "city": account.user.city if account.user else None,
               "session_id": session_id, "session_factory": request.app.state.database.session_factory,
               "vector_store_service": request.app.state.vector_store_service}

    def generate():
        answer = ""
        started = time.monotonic()
        sentiment_service = getattr(request.app.state, "sentiment_service", None) or get_sentiment_service()
        sentiment_future = AUX_EXECUTOR.submit(sentiment_service.analyze, payload.message)
        try:
            runner = getattr(request.app.state, "agent_runner", None) or get_agent_runner()
            for event in runner.stream(messages, context):
                if event.get("event") == "token":
                    answer += event.get("data", {}).get("content", "")
                yield {"event": event["event"], "data": json.dumps(event.get("data", {}), ensure_ascii=False)}
            try:
                remaining = max(0, SENTIMENT_DEADLINE_SECONDS - (time.monotonic() - started))
                sentiment = sentiment_future.result(timeout=remaining)
            except (TimeoutError, Exception):
                sentiment = {"label": "neutral", "score": 0.0}
            with request.app.state.database.session_factory.begin() as write_db:
                stored_user = write_db.get(ConversationHistory, user_message.id)
                stored_user.sentiment = sentiment.get("label", "neutral")
                stored_user.sentiment_score = sentiment.get("score", 0)
                assistant = ConversationHistory(session_id=session_id, user_id=account.user_id, role="assistant", content=answer)
                write_db.add(assistant)
                write_db.flush()
                assistant_id = assistant.id
            yield {"event": "sentiment", "data": json.dumps(sentiment, ensure_ascii=False)}
            if previous_count is None:
                submit_title_generation(request.app, session_id, payload.message, answer)
            followup_service = getattr(request.app.state, "followup_service", None) or get_followup_service()
            followup_future = AUX_EXECUTOR.submit(followup_service.generate, payload.message, answer)
            try:
                questions = followup_future.result(timeout=AUX_TIMEOUT_SECONDS)
            except (TimeoutError, Exception):
                questions = []
            yield {"event": "followup", "data": json.dumps({"questions": questions}, ensure_ascii=False)}
            yield {"event": "done", "data": json.dumps({"message_id": assistant_id})}
        except Exception as exc:
            stored_answer = f"{answer}\n\n[回答中断]" if answer else f"[回答生成失败] {str(exc) or '聊天服务暂不可用'}"
            with request.app.state.database.session_factory.begin() as write_db:
                stored_user = write_db.get(ConversationHistory, user_message.id)
                if stored_user and stored_user.sentiment is None:
                    stored_user.sentiment = "neutral"
                    stored_user.sentiment_score = 0.0
                write_db.add(ConversationHistory(
                    session_id=session_id, user_id=account.user_id, role="assistant", content=stored_answer
                ))
            yield {"event": "error", "data": json.dumps({"message": str(exc) or "聊天服务暂不可用"}, ensure_ascii=False)}

    return EventSourceResponse(generate(), ping=15, headers={"X-Accel-Buffering": "no"})
