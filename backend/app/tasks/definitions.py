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
    """知识向量化：官方知识文件或创作器草稿知识资产进入家庭隔离向量集合。

    创作器资产带 household + skill_version 元数据，与官方/其他家庭检索隔离；
    市场安装以 target_household_id 把已审核知识复制进安装家庭（隔离元数据）。
    """
    from backend.app.api.setup import purpose_ready

    if not purpose_ready(session, "embedding"):
        raise _permanent("EMBEDDING_NOT_CONFIGURED")
    creator_asset_id = payload.get("creator_asset_id")
    if creator_asset_id:
        if payload.get("target_household_id"):
            return _vectorize_market_asset(session, payload, record)
        return _vectorize_creator_asset(session, payload, record)
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


def _vectorize_creator_asset(session, payload, record):
    """创作器知识资产向量化：路径由资产行给出，向量元数据带家庭与技能版本。"""
    from pathlib import Path

    from backend.rag.vector_store import VectorStoreUnavailable, configured_vector_store

    from backend.app.core.config import get_settings
    from backend.app.creator.knowledge import CreatorKnowledgeService
    from backend.app.db.models import CreatorKnowledgeAsset
    from backend.app.db.session import create_database

    asset = session.get(CreatorKnowledgeAsset, payload.get("creator_asset_id"))
    if asset is None or asset.status != "active" or asset.scan_status != "passed":
        raise _permanent("KNOWLEDGE_ASSET_NOT_READY")
    skill_version_id = payload.get("skill_version_id")
    if not skill_version_id:
        raise _permanent("KNOWLEDGE_SKILL_VERSION_REQUIRED")
    settings = get_settings()
    service = configured_vector_store(create_database(settings).session_factory, settings.chroma_path)
    path = Path(settings.knowledge_upload_path) / "creator" / asset.household_id / f"{asset.id}{asset.file_type}"
    if not path.exists():
        raise _permanent("KNOWLEDGE_SOURCE_MISSING")
    try:
        if not service.has_file(asset.id, skill_version_id=skill_version_id):
            service.add_file(asset.id, path, asset.filename, household_id=asset.household_id,
                             skill_version_id=skill_version_id)
    except VectorStoreUnavailable as exc:
        raise _transient("VECTOR_STORE_UNAVAILABLE") from exc
    CreatorKnowledgeService(session, asset.household_id, settings.knowledge_upload_path)
    return {"creator_asset_id": asset.id, "skill_version_id": skill_version_id}


def _vectorize_market_asset(session, payload, record):
    """市场安装知识复制：把已审核资产复制进安装家庭的隔离向量集合。

    源文件留在创作者目录不动；复制到 market/<household>/ 后以安装家庭 +
    本地技能版本元数据入向量库。幂等：has_file 去重，record key 去重。
    """
    import shutil
    from pathlib import Path

    from backend.rag.vector_store import VectorStoreUnavailable, configured_vector_store

    from backend.app.core.config import get_settings
    from backend.app.db.models import CreatorKnowledgeAsset
    from backend.app.db.session import create_database

    asset = session.get(CreatorKnowledgeAsset, payload.get("creator_asset_id"))
    if asset is None or asset.status != "active" or asset.scan_status != "passed":
        raise _permanent("KNOWLEDGE_ASSET_NOT_READY")
    target_household_id = payload.get("target_household_id")
    skill_version_id = payload.get("skill_version_id")
    if not target_household_id or not skill_version_id:
        raise _permanent("KNOWLEDGE_TARGET_INVALID")
    settings = get_settings()
    service = configured_vector_store(create_database(settings).session_factory, settings.chroma_path)
    source = Path(settings.knowledge_upload_path) / "creator" / asset.household_id / f"{asset.id}{asset.file_type}"
    target_dir = Path(settings.knowledge_upload_path) / "market" / target_household_id
    target = target_dir / f"{asset.id}{asset.file_type}"
    if not source.exists():
        raise _permanent("KNOWLEDGE_SOURCE_MISSING")
    if not target.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    try:
        if not service.has_file(asset.id, skill_version_id=skill_version_id):
            service.add_file(asset.id, target, asset.filename, household_id=target_household_id,
                             skill_version_id=skill_version_id)
    except VectorStoreUnavailable as exc:
        raise _transient("VECTOR_STORE_UNAVAILABLE") from exc
    return {"creator_asset_id": asset.id, "skill_version_id": skill_version_id,
            "target_household_id": target_household_id}


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
    from backend.app.llm_runtime.execution import purpose_model_resolver
    from backend.app.llm_runtime.runtime_service import ModelRuntimeError
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
    """沙盒试运行：校验草稿并经安全运行时执行测试用例，持久化逐用例报告。

    兼容 Phase 5 契约：无 draft_id 时仅做声明式定义校验，不执行任何模型或工具。
    """
    from backend.app.creator.sandbox import SandboxTrialError, SandboxTrialService

    draft_id = payload.get("draft_id")
    if not draft_id:
        from backend.agent.platform.versions import DefinitionRejected, validate_agent_definition

        definition = payload.get("definition")
        try:
            validate_agent_definition(definition)
        except DefinitionRejected as exc:
            raise _permanent(f"SANDBOX_{exc.code}") from exc
        return {"validated": True, "note": "definition-only validation (phase 5 contract)"}
    household_id = payload.get("household_id")
    principal_id = payload.get("principal_id")
    if not household_id or not principal_id or not payload.get("report_id"):
        raise _permanent("SANDBOX_PAYLOAD_INVALID")
    from sqlalchemy.orm import sessionmaker

    # 沙盒自行管理事务与里程碑提交，避免在执行器事务上下文内提交。
    service = SandboxTrialService(sessionmaker(bind=session.get_bind(), expire_on_commit=False),
                                  household_id, principal_id)
    try:
        return service.run(payload["report_id"])
    except SandboxTrialError as exc:
        raise _permanent(exc.code) from exc


def models_health_check(session, payload, record):
    """模型健康检查：对启用的模型档案跑最小能力测试并落库证据。"""
    from backend.app.db.models import LlmModel
    from backend.app.llm_runtime.registry import ModelRegistry
    from backend.app.llm_runtime.runtime_service import ModelRuntimeError, ModelRuntimeService

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


def review_precheck(session, payload, record):
    """审核自动预检：完整检查并持久化为审核证据；幂等（同 key 不重复执行）。

    只做预检，不调用任何模型 —— 风险摘要是独立任务，避免在预检事务内
    跨连接写配额。
    """
    from backend.app.review.precheck import ReviewPrecheckService
    from backend.app.db.models import ReviewSubmission

    submission = session.get(ReviewSubmission, payload.get("submission_id"))
    if submission is None:
        raise _permanent("REVIEW_SUBMISSION_NOT_FOUND")
    if submission.status in {"review_pending", "approved", "rejected", "returned"} \
            and submission.precheck_status != "pending":
        return {"submission_id": submission.id, "status": submission.status, "skipped": True}
    evidence = ReviewPrecheckService(session).run(submission)
    return {"submission_id": submission.id, "status": submission.status, "precheck": evidence}


def review_risk_summary(session, payload, record):
    """审核 LLM 风险摘要（仅参考）：review_model 用途 + 家庭配额语义。

    模型未就绪降级为 unavailable（不阻塞队列）；预检尚未完成时按瞬态重试。
    摘要永不改变审核状态。
    """
    from sqlalchemy.orm import sessionmaker

    from backend.app.review.risk import ReviewRiskService, risk_payload
    from backend.app.db.models import ReviewSubmission

    submission = session.get(ReviewSubmission, payload.get("submission_id"))
    if submission is None:
        raise _permanent("REVIEW_SUBMISSION_NOT_FOUND")
    if submission.status == "precheck_pending":
        raise _transient("REVIEW_PRECHECK_PENDING")
    if submission.status != "review_pending":
        service = ReviewRiskService(sessionmaker(bind=session.get_bind(), expire_on_commit=False),
                                    submission.household_id)
        return {"submission_id": submission.id,
                "risk_summary": service._unavailable(submission, "REVIEW_PRECHECK_NOT_PASSED")}
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    service = ReviewRiskService(factory, submission.household_id)
    summary = service.summarize(submission, risk_payload(session, submission))
    return {"submission_id": submission.id, "risk_summary": summary}


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
    "review.precheck": TaskSpec(
        review_precheck, max_attempts=2, description="公开审核自动预检（证据持久化）"),
    "review.risk_summary": TaskSpec(
        review_risk_summary, max_attempts=3, description="审核 LLM 风险摘要（仅参考，不决定通过）"),
}
