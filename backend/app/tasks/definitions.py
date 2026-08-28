"""Registered asynchronous task types for the Celery worker/beat topology.

Every handler receives (session, payload, record) inside the executor
transaction and must keep side effects idempotent by record.idempotency_key.
Handlers avoid module import side effects so SQLite preview/test mode can
register the catalog without importing celery.
"""
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select

from backend.app.db.base import utcnow
from backend.app.db.models import FaqPair, KnowledgeFile, Notification


@dataclass(frozen=True)
class TaskSpec:
    handler: Callable[[Any, dict, Any], dict]
    max_attempts: int = 3
    description: str = ""


def _transient(code):
    from backend.app.tasks.executor import TransientTaskError

    return TransientTaskError(code)


def _permanent(code):
    from backend.app.tasks.executor import PermanentTaskError

    return PermanentTaskError(code)


def reminders_dispatch(session, payload, record):
    """到期提醒：按家庭计算到期 occurrence 并幂等派发通知。"""
    from backend.app.services.household_daily import ReminderScheduleService

    now = utcnow()
    household_id = payload.get("household_id")
    if household_id:
        household_ids = [household_id]
    else:
        from backend.app.db.models import Household
        household_ids = list(session.scalars(select(Household.id)).all())
    service = ReminderScheduleService(session)
    dispatched = []
    for household in household_ids:
        dispatched.extend(service.dispatch_due(household, now=now))
    return {"households": len(household_ids), "occurrences": len(dispatched)}


def notifications_dispatch(session, payload, record):
    household_id = payload.get("household_id")
    if not household_id:
        raise _permanent("NOTIFICATION_HOUSEHOLD_REQUIRED")
    source_id = str(payload.get("source_id") or "")
    if source_id:
        existing = session.scalar(select(Notification).where(
            Notification.household_id == household_id,
            Notification.source_type == str(payload.get("source_type") or ""),
            Notification.source_id == source_id))
        if existing is not None:
            return {"notification_id": existing.id, "deduped": True}
    from backend.app.services.household_daily import NotificationService

    notification = NotificationService(session).create(
        household_id, title=payload["title"], body=payload.get("body", ""), kind=payload.get("kind", "general"),
        source_type=payload.get("source_type", ""), source_id=source_id)
    return {"notification_id": notification.id, "deduped": False}


def knowledge_vectorize(session, payload, record):
    """知识向量化：把已激活知识文件补偿进家庭隔离向量集合。"""
    from backend.rag.vector_store import VectorStoreUnavailable

    from backend.app.core.config import get_settings
    from backend.app.db.session import create_database

    file_id = payload.get("file_id")
    item = session.get(KnowledgeFile, file_id) if file_id else None
    if item is None or item.status != "active":
        raise _permanent("KNOWLEDGE_FILE_NOT_FOUND")
    settings = get_settings()
    database = create_database(settings)
    from backend.rag.vector_store import configured_vector_store

    service = configured_vector_store(database.session_factory, settings.chroma_path)
    from backend.app.api.knowledge import _knowledge_file_path, official_knowledge_skill_version

    path = _knowledge_file_path(settings, item)
    if path is None:
        raise _permanent("KNOWLEDGE_SOURCE_MISSING")
    try:
        if not service.has_file(item.id):
            skill_version_id = official_knowledge_skill_version(session, item.household_id)
            service.add_file(item.id, path, item.filename, household_id=item.household_id,
                             skill_version_id=skill_version_id)
    except VectorStoreUnavailable as exc:
        raise _transient("VECTOR_STORE_UNAVAILABLE") from exc
    return {"file_id": item.id, "filename": item.filename}


def faq_generate(session, payload, record):
    """FAQ 生成：对指定知识文件生成待审核 FAQ 对。"""
    file_id = payload.get("file_id")
    item = session.get(KnowledgeFile, file_id) if file_id else None
    if item is None:
        raise _permanent("KNOWLEDGE_FILE_NOT_FOUND")
    from backend.app.api.knowledge import _knowledge_file_path
    from backend.app.core.config import get_settings

    path = _knowledge_file_path(get_settings(), item)
    if path is None:
        raise _permanent("KNOWLEDGE_SOURCE_MISSING")
    from backend.app.models.execution import purpose_model_resolver
    from backend.app.models.runtime_service import ModelRuntimeError
    from backend.app.services.faq_gen import FaqGenerationError, FaqGenerator

    resolver = purpose_model_resolver(_session_factory(), item.household_id)
    try:
        pairs = FaqGenerator(model_resolver=resolver).generate(path)
    except (FaqGenerationError, ModelRuntimeError) as exc:
        raise _transient("FAQ_MODEL_UNAVAILABLE") from exc
    created = 0
    for pair in pairs:
        question, answer = pair.get("question", "").strip(), pair.get("answer", "").strip()
        if not question or not answer:
            continue
        existing = session.scalar(select(FaqPair).where(FaqPair.household_id == item.household_id,
                                                        FaqPair.source_file_id == item.id,
                                                        FaqPair.question == question))
        if existing is None:
            session.add(FaqPair(household_id=item.household_id, question=question, answer=answer,
                                source_file_id=item.id, status="pending"))
            created += 1
    return {"file_id": item.id, "generated": len(pairs), "created": created}


def _session_factory():
    from backend.app.core.config import get_settings
    from backend.app.db.session import create_database

    return create_database(get_settings()).session_factory


def sandbox_trial(session, payload, record):
    """沙盒试运行（Phase 5 版）：仅做声明式定义安全校验，不执行任何模型或工具。"""
    from backend.agent.platform.versions import DefinitionRejected, validate_agent_definition

    definition = payload.get("definition")
    try:
        validate_agent_definition(definition)
    except DefinitionRejected as exc:
        raise _permanent(f"SANDBOX_{exc.code}") from exc
    return {"validated": True, "note": "sandbox trial is validation-only in phase 5"}


def models_health_check(session, payload, record):
    """模型健康检查：对启用的模型档案跑最小能力测试并落库证据。"""
    from backend.app.db.models import LlmModel
    from backend.app.models.registry import ModelRegistry
    from backend.app.models.runtime_service import ModelRuntimeError, ModelRuntimeService

    model_id = payload.get("model_id")
    if model_id:
        models = [session.get(LlmModel, model_id)]
    else:
        models = list(session.scalars(select(LlmModel).where(LlmModel.enabled.is_(True))).all())
    models = [model for model in models if model is not None]
    if not models:
        return {"models": 0, "note": "no enabled models"}
    results = []
    for model in models:
        try:
            tester = ModelRuntimeService(session).capability_tester(model)
            evidence = (tester.run_embedding(model.embedding_dimensions) if model.model_type == "embedding"
                        else tester.run_chat())
        except ModelRuntimeError as exc:
            raise _transient("MODEL_HEALTH_CHECK_FAILED") from exc
        ModelRegistry(session).record_capability_result(
            model.id, evidence.capabilities, evidence.error_code,
            {"event_types": [event.type for event in evidence.events], "dimensions": evidence.dimensions,
             "source": "celery"})
        results.append({"model_id": model.id, "passed": evidence.passed})
    return {"models": len(results), "results": results}


def _safe_json(value):
    import json

    try:
        return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


TASK_TYPES = {
    "household.reminders.dispatch": TaskSpec(
        reminders_dispatch, max_attempts=3, description="到期提醒派发（幂等 occurrence）"),
    "household.notifications.dispatch": TaskSpec(
        notifications_dispatch, max_attempts=3, description="站内通知派发（source_id 幂等）"),
    "knowledge.vectorize": TaskSpec(
        knowledge_vectorize, max_attempts=3, description="知识向量化入队"),
    "content.faq_generate": TaskSpec(
        faq_generate, max_attempts=2, description="FAQ 生成（模型辅助，失败可重试）"),
    "creator.sandbox_trial": TaskSpec(
        sandbox_trial, max_attempts=1, description="创作沙盒试运行（仅校验，不执行）"),
    "models.health_check": TaskSpec(
        models_health_check, max_attempts=2, description="模型档案健康检查"),
}
