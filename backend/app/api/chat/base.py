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
from backend.app.db.models import Account, AgentDefinition, AgentDeployment, AgentInstallation, AgentRun, AgentVersion, ChatSession, ConversationHistory, HouseholdMember, Ticket, ToolDefinition, ToolPermission
from backend.agent.platform.context import RunBudget, RunContext
from backend.agent.tools.household_gateways import HouseholdToolRepositories
from backend.agent.platform.runner import DeploymentAgentRunner
from backend.app.services.defaults import get_agent_runner, get_followup_service, get_sentiment_service, get_title_service
from backend.app.api.setup import purpose_ready, setup_status
from backend.app.llm_runtime.adapters import build_langchain_chat
from backend.app.llm_runtime.runtime_service import ModelRuntimeError, ModelRuntimeService
from backend.app.llm_runtime.quotas import QuotaExceeded, QuotaService
from backend.app.llm_runtime.execution import purpose_model_resolver


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
    deployment_id: str | None = Field(default=None, max_length=36)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class FeedbackRequest(BaseModel):
    feedback: str = Field(pattern="^(like|dislike)$")


def session_dict(item):
    return {"id": item.id, "title": item.title, "deployment_id": item.deployment_id,
            "created_at": item.created_at, "updated_at": item.updated_at}


def official_device_deployment(db, household_id):
    return db.scalar(select(AgentDeployment).join(AgentVersion,
        AgentVersion.id == AgentDeployment.agent_version_id).join(AgentDefinition,
        AgentDefinition.id == AgentVersion.agent_id).join(AgentInstallation,
        AgentInstallation.deployment_id == AgentDeployment.id).where(
            AgentDeployment.household_id == household_id, AgentDefinition.name == "设备管家",
            AgentInstallation.household_id == household_id, AgentInstallation.status == "installed",
            AgentInstallation.authority_slot == "active",
            AgentInstallation.agent_version_id == AgentDeployment.agent_version_id).order_by(AgentDeployment.id))


def owned_session(db, session_id, account_id):
    item = db.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.account_id == account_id))
    if not item:
        raise HTTPException(status_code=404, detail="会话不存在")
    return item


def exactly_one_household(household_ids):
    values = list(household_ids)
    if not values: raise HTTPException(409, detail={"code": "HOUSEHOLD_MEMBERSHIP_MISSING"})
    if len(values) != 1: raise HTTPException(409, detail={"code": "HOUSEHOLD_MEMBERSHIP_AMBIGUOUS"})
    return values[0]
