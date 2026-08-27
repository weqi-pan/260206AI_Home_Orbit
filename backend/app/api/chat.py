import asyncio
import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, AgentDeployment, AgentRun, AgentVersion, ChatSession, ConversationHistory, HouseholdMember, Ticket, ToolDefinition, ToolPermission
from backend.agent.platform.context import RunContext
from backend.agent.tools.gateways import ToolCapabilities, ToolRepositories
from backend.agent.platform.runner import DeploymentAgentRunner
from backend.app.services.defaults import get_agent_runner, get_followup_service, get_sentiment_service, get_title_service
from backend.app.api.setup import setup_status
from backend.app.models.adapters import build_langchain_chat
from backend.app.models.runtime_service import ModelRuntimeError, ModelRuntimeService
from backend.app.models.quotas import QuotaExceeded, QuotaService
from backend.app.models.execution import purpose_model_resolver


router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)
AUX_EXECUTOR = ThreadPoolExecutor(max_workers=8)
TITLE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat-title")
SENTIMENT_DEADLINE_SECONDS = 3.0
DEFAULT_FOLLOWUP_TIMEOUT_SECONDS = 4.0
SENTIMENT_DISCONNECT_GRACE_SECONDS = 0.5

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


def submit_title_generation(app, session_id: str, user_text: str, answer: str, model_resolver=None) -> Future | None:
    title_service = getattr(app.state, "title_service", None) or get_title_service(model_resolver)
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
    household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
    item = ChatSession(account_id=account.id, household_id=household_id, title=payload.title)
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
    owned_session(db, session_id, account.id)
    chat_session = db.get(ChatSession, session_id)
    household_id = chat_session.household_id
    injected_runner = getattr(request.app.state, "agent_runner", None)
    resolved_runtime = None
    usage_id = None
    enforce_runtime = injected_runner is None or getattr(request.app.state, "enforce_model_runtime_for_injected_runner", False)
    if enforce_runtime:
        if not setup_status(db, account).ready:
            raise HTTPException(status_code=503, detail={"code": "MODEL_NOT_CONFIGURED"})
        try:
            resolved_runtime = ModelRuntimeService(db).resolve("primary_chat", household_id=household_id)
        except ModelRuntimeError as exc:
            status_code = 403 if exc.code == "MODEL_NOT_GRANTED" else 503
            raise HTTPException(status_code=status_code, detail={"code": exc.code}) from exc
        profile = resolved_runtime.snapshot.profile
        request_budget = profile["agent_token_budget"] or profile["max_tokens"]
        if not request_budget:
            raise HTTPException(status_code=503, detail={"code": "MODEL_REQUEST_BUDGET_NOT_CONFIGURED"})
        try:
            usage_id = QuotaService(db).reserve(household_id, resolved_runtime.snapshot.model_id, request_budget).id
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail={"code": exc.code}) from exc
    previous_count = db.scalar(select(ConversationHistory.id).where(ConversationHistory.session_id == session_id).limit(1))
    user_message = ConversationHistory(session_id=session_id, user_id=account.user_id, role="user", content=payload.message)
    db.add(user_message)
    db.commit()
    history = db.scalars(select(ConversationHistory).where(ConversationHistory.session_id == session_id).order_by(ConversationHistory.id)).all()
    messages = [{"role": item.role, "content": item.content} for item in history]
    context = {"account_id": account.id, "user_id": account.user_id, "city": account.user.city if account.user else None,
               "session_id": session_id, "repositories": ToolCapabilities.bind(ToolRepositories(request.app.state.database.session_factory)),
               "vector_store_service": request.app.state.vector_store_service}
    if enforce_runtime:
        context["model_resolver"] = purpose_model_resolver(request.app.state.database.session_factory, household_id)
    if resolved_runtime:
        profile = resolved_runtime.snapshot.profile
        context["model_snapshot"] = resolved_runtime.snapshot
        builder = getattr(request.app.state, "chat_model_builder", None)
        try:
            context["chat_model"] = builder(resolved_runtime) if builder else ModelRuntimeService(db).build_chat_model(resolved_runtime)
        except Exception as exc:
            if usage_id:
                QuotaService(db).complete(usage_id, status="failed")
            raise HTTPException(status_code=503, detail={"code": "MODEL_PROVIDER_UNAVAILABLE"}) from exc
    if chat_session.deployment_id:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account.id,
                                                          HouseholdMember.household_id == household_id))
        deployment = db.scalar(select(AgentDeployment).where(AgentDeployment.id == chat_session.deployment_id,
                                                              AgentDeployment.household_id == household_id))
        version = db.get(AgentVersion, deployment.agent_version_id) if deployment else None
        if not member or not deployment or not version:
            raise HTTPException(status_code=404, detail={"code": "DEPLOYMENT_NOT_FOUND"})
        snapshot = resolved_runtime.snapshot if resolved_runtime else None
        from backend.agent.platform.authority import ExecutionAuthority
        from backend.agent.platform.snapshots import CompiledSnapshotService
        installation = ExecutionAuthority(db).installation(deployment.id, household_id, version.id)[1]
        provisional_context = RunContext(principal_id=account.id, household_id=household_id, member_id=member.id,
            role=member.role, session_id=session_id, deployment_id=deployment.id, agent_version_id=version.id,
            run_id="pending", timezone="Asia/Shanghai", grants={}, consent={}, deployment_revision=deployment.revision,
            model_id=snapshot.model_id if snapshot else "injected-model",
            model_revision=snapshot.config_revision if snapshot else 1,
            model_snapshot={"id": snapshot.model_id, "revision": snapshot.config_revision,
                            "profile": dict(snapshot.profile)} if snapshot else {"id": "injected-model", "revision": 1})
        compiled_snapshot = CompiledSnapshotService(db).capture(deployment.id, installation.id, provisional_context)
        run = AgentRun(household_id=household_id, deployment_id=deployment.id, principal_id=account.id,
                       member_id=member.id, session_id=session_id, agent_version_id=version.id,
                       deployment_revision=deployment.revision, model_id=snapshot.model_id if snapshot else "injected-model",
                       model_revision=snapshot.config_revision if snapshot else 1,
                       compiled_snapshot_json=json.dumps(compiled_snapshot, ensure_ascii=False, sort_keys=True),
                       model_snapshot_json=json.dumps(provisional_context.model_snapshot, sort_keys=True),
                       permission_snapshot_json=json.dumps(dict(compiled_snapshot["grants"]), sort_keys=True),
                       consent_snapshot_json="{}", budget_snapshot_json=json.dumps(
                           compiled_snapshot["agent_version"]["definition"].get("budgets", {}), sort_keys=True))
        db.add(run); db.commit(); db.refresh(run)
        grants = dict(compiled_snapshot["grants"])
        context = RunContext(
            principal_id=account.id, household_id=household_id, member_id=member.id, role=member.role,
            session_id=session_id, deployment_id=deployment.id, agent_version_id=version.id, run_id=run.id,
            timezone="Asia/Shanghai", grants=grants, consent={}, deployment_revision=deployment.revision,
            model_id=run.model_id, model_revision=run.model_revision, user_id=account.user_id,
            model_snapshot=json.loads(run.model_snapshot_json), compiled_snapshot=compiled_snapshot,
            skill_version_ids=tuple(item["id"] for item in compiled_snapshot["skills"]),
            city=account.user.city if account.user else None,
            repositories=ToolCapabilities.bind(ToolRepositories(request.app.state.database.session_factory)),
            vector_store_service=request.app.state.vector_store_service,
            chat_model=context.get("chat_model"),
        )
    runner_override = injected_runner
    if isinstance(context, RunContext) and runner_override is None:
        runner_override = request.app.state.deployment_agent_runner
    return EventSourceResponse(
        chat_event_stream(request.app, session_id, account, payload.message, messages, context, user_message.id,
                          previous_count is None, usage_id=usage_id, runner_override=runner_override),
        ping=15,
        headers={"X-Accel-Buffering": "no"},
    )

def chat_event_stream(app, session_id, account, message, messages, context, user_message_id, first_message, usage_id=None,
                      runner_override=None):
    answer = ""
    persisted_assistant_id = None
    started = time.monotonic()
    model_resolver = context.get("model_resolver") if isinstance(context, dict) else None
    sentiment_service = getattr(app.state, "sentiment_service", None) or get_sentiment_service(model_resolver)
    sentiment_future = AUX_EXECUTOR.submit(sentiment_service.analyze, message)
    try:
        runner = runner_override or getattr(app.state, "agent_runner", None) or get_agent_runner()
        for event in runner.stream(messages, context):
            if event.get("event") == "token":
                answer += event.get("data", {}).get("content", "")
            yield {"event": event["event"], "data": json.dumps(event.get("data", {}), ensure_ascii=False)}
        try:
            remaining = max(0, SENTIMENT_DEADLINE_SECONDS - (time.monotonic() - started))
            sentiment = sentiment_future.result(timeout=remaining)
        except Exception:
            sentiment = {"label": "neutral", "score": 0.0}
        with app.state.database.session_factory.begin() as write_db:
            stored_user = write_db.get(ConversationHistory, user_message_id)
            stored_user.sentiment = sentiment.get("label", "neutral")
            stored_user.sentiment_score = sentiment.get("score", 0)
            assistant = ConversationHistory(session_id=session_id, user_id=account.user_id, role="assistant", content=answer)
            write_db.add(assistant)
            write_db.flush()
            assistant_id = assistant.id
        persisted_assistant_id = assistant_id
        yield {"event": "sentiment", "data": json.dumps(sentiment, ensure_ascii=False)}
        if first_message:
            submit_title_generation(app, session_id, message, answer, model_resolver)
        followup_service = getattr(app.state, "followup_service", None) or get_followup_service(model_resolver)
        followup_future = AUX_EXECUTOR.submit(followup_service.generate, message, answer)
        followup_timeout = float(getattr(getattr(app.state, "settings", None), "followup_timeout_seconds", DEFAULT_FOLLOWUP_TIMEOUT_SECONDS))
        try:
            questions = followup_future.result(timeout=followup_timeout)
        except Exception:
            questions = []
        yield {"event": "followup", "data": json.dumps({"questions": questions}, ensure_ascii=False)}
        if usage_id:
            with app.state.database.session_factory() as usage_db:
                QuotaService(usage_db).complete(usage_id)
        yield {"event": "done", "data": json.dumps({"message_id": assistant_id})}
    except (GeneratorExit, asyncio.CancelledError):
        _persist_disconnected_answer(app, session_id, account.user_id, user_message_id, answer, sentiment_future, persisted_assistant_id)
        if usage_id:
            with app.state.database.session_factory() as usage_db:
                QuotaService(usage_db).complete(usage_id, status="cancelled")
        raise
    except Exception as exc:
        stored_answer = f"{answer}\n\n[回答中断]" if answer else "[回答生成失败] 聊天服务暂不可用"
        with app.state.database.session_factory.begin() as write_db:
            stored_user = write_db.get(ConversationHistory, user_message_id)
            if stored_user and stored_user.sentiment is None:
                stored_user.sentiment = "neutral"
                stored_user.sentiment_score = 0.0
            write_db.add(ConversationHistory(
                session_id=session_id, user_id=account.user_id, role="assistant", content=stored_answer
            ))
        if usage_id:
            with app.state.database.session_factory() as usage_db:
                QuotaService(usage_db).complete(usage_id, status="failed")
        yield {"event": "error", "data": json.dumps({"code": "MODEL_EXECUTION_FAILED", "message": "聊天服务暂不可用"}, ensure_ascii=False)}


def _persist_disconnected_answer(app, session_id, user_id, user_message_id, answer, sentiment_future, persisted_assistant_id=None):
    """客户端断开兜底：assistant 已持久化则只补写 sentiment，否则持久化部分回答或明确占位。"""
    content = f"{answer}\n\n[回答中断]" if answer else "[回答生成失败] 客户端提前断开"
    try:
        sentiment = sentiment_future.result(timeout=SENTIMENT_DISCONNECT_GRACE_SECONDS)
    except Exception:
        sentiment = {"label": "neutral", "score": 0.0}
    try:
        with app.state.database.session_factory.begin() as write_db:
            stored_user = write_db.get(ConversationHistory, user_message_id)
            if stored_user and stored_user.sentiment is None:
                stored_user.sentiment = sentiment.get("label", "neutral")
                stored_user.sentiment_score = sentiment.get("score", 0)
            if persisted_assistant_id is None:
                write_db.add(ConversationHistory(
                    session_id=session_id, user_id=user_id, role="assistant", content=content
                ))
    except Exception:
        logger.exception("客户端断开兜底持久化失败 session_id=%s", session_id)
