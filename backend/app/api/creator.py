# -*- coding: utf-8 -*-
"""Phase 6 /api/creator surface.

Household-scoped creator API: draft lifecycle (draft -> validated ->
private_published), NL generation, platform validation, knowledge assets,
sandbox trial (async task + poll; synchronous documented boundary in SQLite
mode), private publishing and my-published listing. Stable 403/404 codes.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.creator.generation import GenerationError, IntentCardGenerator
from backend.app.creator.knowledge import CreatorKnowledgeService, KnowledgeAssetRejected
from backend.app.creator.publishing import PrivatePublishService, PublishRejected
from backend.app.creator.sandbox import SandboxTrialError, SandboxTrialService
from backend.app.api.setup import purpose_ready
from backend.app.creator.validation import DraftValidationRejected, DraftValidator
from backend.app.creator.schemas import (DraftCreateRequest, DraftUpdateRequest, GenerateRequest,
                                         PublishRequest, TrialRequest)
from backend.app.db.base import utcnow
from backend.app.db.models import Account, AgentVersion, CreatorDraft, CreatorTrialReport, HouseholdMember, ReviewSubmission
from backend.app.schemas.market import SubmitReviewRequest

router = APIRouter(prefix="/creator", tags=["creator"])

WRITER_ROLES = {"owner", "admin", "member"}
PUBLISH_ROLES = {"owner", "admin"}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


def _household(db, account_id) -> HouseholdMember:
    rows = list(db.scalars(select(HouseholdMember).where(HouseholdMember.account_id == account_id)).all())
    if not rows:
        raise HTTPException(404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    if len(rows) != 1:
        raise HTTPException(409, detail={"code": "HOUSEHOLD_MEMBERSHIP_AMBIGUOUS"})
    return rows[0]


def _draft(db, household_id, draft_id) -> CreatorDraft:
    draft = db.scalar(select(CreatorDraft).where(CreatorDraft.id == draft_id,
                                                 CreatorDraft.household_id == household_id))
    if draft is None:
        raise HTTPException(404, detail={"code": "DRAFT_NOT_FOUND"})
    return draft


REVIEW_LOCKED_SUBMISSION_STATUSES = ("precheck_pending", "review_pending")


def _guard_draft_not_locked_in_review(db, household_id, draft_id) -> None:
    """提交处于预检/人工审核窗口期时锁定草稿内容编辑，防止预检证据与冻结工件发散。"""
    from backend.app.db.models import ReviewSubmission

    locked = db.scalar(select(ReviewSubmission.id).where(
        ReviewSubmission.household_id == household_id,
        ReviewSubmission.draft_id == draft_id,
        ReviewSubmission.status.in_(REVIEW_LOCKED_SUBMISSION_STATUSES)).limit(1))
    if locked is not None:
        raise HTTPException(409, detail={"code": "REVIEW_SUBMISSION_LOCKED"})


def _draft_view(db, household_id, draft):
    assets = CreatorKnowledgeService(db, household_id, "").list_assets(draft.id)
    versions = []
    if draft.agent_definition_id:
        versions = [{"id": row.id, "version": row.version, "published_at": row.published_at}
                    for row in db.scalars(select(AgentVersion).where(
                        AgentVersion.agent_id == draft.agent_definition_id).order_by(AgentVersion.version)).all()]
    latest_trial = db.scalar(select(CreatorTrialReport).where(
        CreatorTrialReport.draft_id == draft.id).order_by(CreatorTrialReport.created_at.desc(),
                                                          CreatorTrialReport.id.desc()))
    return {
        "id": draft.id, "title": draft.title, "status": draft.status,
        "description": draft.source_description,
        "intent_card": json.loads(draft.intent_card_json) if draft.intent_card_json else None,
        "validation": json.loads(draft.last_validation_json) if draft.last_validation_json else None,
        "knowledge_assets": [{"id": item.id, "filename": item.filename, "file_type": item.file_type,
                              "size_bytes": item.size_bytes, "skill_key": item.skill_key,
                              "scan_status": item.scan_status, "scan_reason": item.scan_reason,
                              "content_hash": item.content_hash} for item in assets],
        "agent_versions": versions,
        "deployment_id": draft.deployment_id,
        "latest_trial": {"id": latest_trial.id, "status": latest_trial.status,
                         "error_code": latest_trial.error_code} if latest_trial else None,
        "created_at": draft.created_at, "updated_at": draft.updated_at,
    }


@router.get("/tools")
def ceiling_tools(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return sorted(DraftValidator(db, member.household_id).ceiling_tools().values(), key=lambda item: item["key"])


@router.post("/drafts", status_code=201)
def create_draft(payload: DraftCreateRequest, account: Account = Depends(get_current_account),
                 db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = CreatorDraft(household_id=member.household_id, title=payload.title,
                         source_description=payload.description, created_by_account_id=account.id)
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return _draft_view(db, member.household_id, draft)


@router.get("/drafts")
def list_drafts(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    rows = db.scalars(select(CreatorDraft).where(CreatorDraft.household_id == member.household_id)
                      .order_by(CreatorDraft.updated_at.desc(), CreatorDraft.id.desc())).all()
    return [{"id": row.id, "title": row.title, "status": row.status, "updated_at": row.updated_at} for row in rows]


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return _draft_view(db, member.household_id, _draft(db, member.household_id, draft_id))


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: str, payload: DraftUpdateRequest, account: Account = Depends(get_current_account),
                 db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    _guard_draft_not_locked_in_review(db, member.household_id, draft_id)
    if payload.title is not None:
        draft.title = payload.title
    if payload.intent_card is not None:
        # 高级区保存同一校验边界：非法意图卡拒绝保存，绝不入库
        report = _validation_report(db, member.household_id, draft, payload.intent_card)
        if not report["valid"]:
            raise HTTPException(422, detail={"code": "DRAFT_VALIDATION_FAILED", "errors": report["errors"]})
        draft.intent_card_json = json.dumps(payload.intent_card, ensure_ascii=False)
        draft.last_validation_json = json.dumps(report, ensure_ascii=False)
        draft.status = "draft"
    draft.updated_at = utcnow()
    db.commit()
    db.refresh(draft)
    return _draft_view(db, member.household_id, draft)


def _validation_report(db, household_id, draft, card):
    validator = DraftValidator(db, household_id)
    try:
        validator.validate(card, draft_id=draft.id)
        return {"valid": True, "errors": []}
    except DraftValidationRejected as exc:
        return {"valid": False, "errors": exc.errors}


@router.post("/drafts/{draft_id}/generate")
def generate_card(draft_id: str, payload: GenerateRequest, request: Request,
                  account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    from backend.app.models.execution import purpose_model_resolver
    from backend.app.models.quotas import QuotaExceeded
    from backend.app.models.runtime_service import ModelRuntimeError

    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    _guard_draft_not_locked_in_review(db, member.household_id, draft_id)
    validator = DraftValidator(db, member.household_id)
    generator = getattr(request.app.state, "intent_card_generator", None) or IntentCardGenerator(
        model_resolver=purpose_model_resolver(request.app.state.database.session_factory, member.household_id))
    try:
        card = generator.generate(payload.description, catalog=validator.ceiling_tools())
    except GenerationError as exc:
        status_code = 429 if "QUOTA" in exc.code or "RATE" in exc.code else 403 if "GRANT" in exc.code else 503
        raise HTTPException(status_code, detail={"code": exc.code}) from exc
    except (ModelRuntimeError, QuotaExceeded) as exc:
        status_code = 429 if "QUOTA" in exc.code or "RATE" in exc.code else 403 if "GRANT" in exc.code else 503
        raise HTTPException(status_code, detail={"code": exc.code}) from exc
    report = _validation_report(db, member.household_id, draft, card)
    draft.source_description = payload.description
    draft.intent_card_json = json.dumps(card, ensure_ascii=False)
    draft.last_validation_json = json.dumps(report, ensure_ascii=False)
    if not report["valid"]:
        draft.status = "draft"
    db.commit()
    db.refresh(draft)
    return {"intent_card": card, "validation": report,
            "status": draft.status}


@router.post("/drafts/{draft_id}/validate")
def validate_draft(draft_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    if not draft.intent_card_json:
        raise HTTPException(409, detail={"code": "DRAFT_EMPTY"})
    card = json.loads(draft.intent_card_json)
    report = _validation_report(db, member.household_id, draft, card)
    draft.last_validation_json = json.dumps(report, ensure_ascii=False)
    if report["valid"]:
        draft.status = "validated"
    else:
        draft.status = "draft"
        raise HTTPException(422, detail={"code": "DRAFT_VALIDATION_FAILED", "errors": report["errors"]})
    db.commit()
    db.refresh(draft)
    return {"status": draft.status, "validation": report}


@router.post("/drafts/{draft_id}/knowledge", status_code=201)
def upload_knowledge(draft_id: str, request: Request, file: UploadFile = File(...),
                     skill_key: str = Form(default=""), account: Account = Depends(get_current_account),
                     db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    _draft(db, member.household_id, draft_id)
    content = file.file.read(MAX_UPLOAD_SIZE + 1)
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, detail={"code": "KNOWLEDGE_TOO_LARGE"})
    service = CreatorKnowledgeService(db, member.household_id,
                                       request.app.state.settings.knowledge_upload_path)
    try:
        asset = service.register_asset(draft_id, file.filename or "upload.txt",
                                       Path(file.filename or "").suffix, content,
                                       skill_key=skill_key, uploaded_by=account.id)
    except KnowledgeAssetRejected as exc:
        status_code = {"DRAFT_NOT_FOUND": 404, "KNOWLEDGE_DUPLICATE": 409}.get(exc.code, 422)
        raise HTTPException(status_code, detail={"code": exc.code, "reason": exc.detail}) from exc
    return {"id": asset.id, "filename": asset.filename, "file_type": asset.file_type,
            "size_bytes": asset.size_bytes, "skill_key": asset.skill_key,
            "status": asset.status, "scan_status": asset.scan_status, "scan_reason": asset.scan_reason,
            "content_hash": asset.content_hash}


@router.get("/drafts/{draft_id}/knowledge")
def list_knowledge(draft_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    _draft(db, member.household_id, draft_id)
    service = CreatorKnowledgeService(db, member.household_id, "")
    return [{"id": item.id, "filename": item.filename, "file_type": item.file_type,
             "size_bytes": item.size_bytes, "skill_key": item.skill_key,
             "scan_status": item.scan_status, "scan_reason": item.scan_reason,
             "content_hash": item.content_hash} for item in service.list_assets(draft_id)]


@router.delete("/drafts/{draft_id}/knowledge/{asset_id}", status_code=204)
def delete_knowledge(draft_id: str, asset_id: str, request: Request,
                     account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    _draft(db, member.household_id, draft_id)
    # 冻结工件按资产 id 引用知识：审核窗口期删除会使已审工件缺源
    _guard_draft_not_locked_in_review(db, member.household_id, draft_id)
    service = CreatorKnowledgeService(db, member.household_id, request.app.state.settings.knowledge_upload_path)
    try:
        service.delete_asset(draft_id, asset_id,
                             vector_service=(getattr(request.app.state, "vector_store_service", None)
                                             if purpose_ready(db, "embedding") else None))
    except KnowledgeAssetRejected as exc:
        raise HTTPException(404, detail={"code": exc.code}) from exc
    return None


@router.post("/drafts/{draft_id}/trial")
def run_trial(draft_id: str, payload: TrialRequest, request: Request,
              account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in WRITER_ROLES:
        raise HTTPException(403, detail={"code": "CREATOR_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    service = SandboxTrialService(request.app.state.database.session_factory, member.household_id, account.id)
    try:
        report = service.create_report(draft.id, payload.granted_tool_keys)
    except SandboxTrialError as exc:
        raise HTTPException(404, detail={"code": exc.code}) from exc
    from backend.app.tasks.executor import TaskExecutor

    task_payload = {"draft_id": draft.id, "report_id": report.id, "household_id": member.household_id,
                    "principal_id": account.id, "granted_tool_keys": payload.granted_tool_keys}
    key = f"sandbox-trial:{draft.id}:{report.id}"
    settings = request.app.state.settings
    if settings.app_mode != "production":
        executor = TaskExecutor(request.app.state.database.session_factory)
        executor.submit("creator.sandbox_trial", task_payload, key, household_id=member.household_id)
        record = executor.run(key)
        summary = _trial_summary(db, member.household_id, report.id)
        return {"report_id": report.id, "task_id": record.id, "status": record.status,
                "error_code": record.error_code, "cases": summary["cases"]}
    from backend.app.tasks.worker_tasks import dispatch_sandbox_trial

    dispatch_sandbox_trial(draft.id, report.id, member.household_id, account.id, payload.granted_tool_keys)
    return {"report_id": report.id, "status": "pending"}


def _trial_summary(db, household_id, report_id):
    report = db.scalar(select(CreatorTrialReport).where(CreatorTrialReport.id == report_id,
                                                        CreatorTrialReport.household_id == household_id))
    if report is None:
        raise HTTPException(404, detail={"code": "TRIAL_REPORT_NOT_FOUND"})
    return {"id": report.id, "draft_id": report.draft_id, "status": report.status,
            "cases": json.loads(report.cases_json or "[]"), "error_code": report.error_code,
            "granted_tool_keys": json.loads(report.granted_tool_keys_json or "[]"),
            "created_at": report.created_at, "finished_at": report.finished_at}


@router.get("/drafts/{draft_id}/trials/{report_id}")
def trial_status(draft_id: str, report_id: str, account: Account = Depends(get_current_account),
                 db: Session = Depends(get_db)):
    member = _household(db, account.id)
    _draft(db, member.household_id, draft_id)
    return _trial_summary(db, member.household_id, report_id)


@router.post("/drafts/{draft_id}/publish", status_code=201)
def publish_draft(draft_id: str, payload: PublishRequest, request: Request,
                  account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    if member.role not in PUBLISH_ROLES:
        raise HTTPException(403, detail={"code": "PUBLISH_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    if draft.status != "validated":
        raise HTTPException(409, detail={"code": "DRAFT_NOT_VALIDATED"})
    service = PrivatePublishService(request.app.state.database.session_factory, member.household_id, account.id,
                                    settings=request.app.state.settings)
    try:
        result = service.publish(draft_id, payload.granted_tool_keys)
    except PublishRejected as exc:
        status_code = {"DRAFT_NOT_FOUND": 404, "DRAFT_NOT_VALIDATED": 409}.get(exc.code, 422)
        raise HTTPException(status_code, detail={"code": exc.code, "reason": exc.detail}) from exc
    db.commit()
    return result


@router.post("/drafts/{draft_id}/submit-review", status_code=201)
def submit_review(draft_id: str, payload: SubmitReviewRequest, request: Request,
                  account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    """提交公开审核：固化不可变版本并触发自动预检（SQLite 模式同步手动边界）。"""
    from backend.app.review.service import ReviewError, ReviewGovernanceService

    member = _household(db, account.id)
    if member.role not in PUBLISH_ROLES:
        raise HTTPException(403, detail={"code": "PUBLISH_ROLE_FORBIDDEN"})
    draft = _draft(db, member.household_id, draft_id)
    governance = ReviewGovernanceService(db)
    try:
        submission = governance.submit(draft, account.id, payload.category)
    except ReviewError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    db.commit()
    _run_review_tasks(request, submission)
    db.expire_all()
    fresh = db.get(ReviewSubmission, submission.id)
    return _submission_view(fresh)


def _run_review_tasks(request, submission):
    """Dispatch review.precheck (+ review.risk_summary when queued).

    SQLite/test mode runs both records synchronously through the TaskExecutor
    (documented manual boundary); production dispatches Celery tasks where the
    risk task retries transiently until the precheck task has committed.
    """
    attempt = submission.precheck_attempt + 1
    settings = request.app.state.settings
    if settings.app_mode != "production":
        from backend.app.tasks.executor import TaskExecutor

        executor = TaskExecutor(request.app.state.database.session_factory)
        key = f"review.precheck:{submission.id}:{attempt}"
        executor.submit("review.precheck", {"submission_id": submission.id}, key,
                        household_id=submission.household_id)
        executor.run(key)
        db = request.app.state.database.session_factory()
        try:
            fresh = db.get(ReviewSubmission, submission.id)
            if fresh.status == "review_pending":
                risk_key = f"review.risk_summary:{submission.id}:{attempt}"
                executor.submit("review.risk_summary", {"submission_id": submission.id}, risk_key,
                                household_id=submission.household_id)
                executor.run(risk_key)
        finally:
            db.close()
        return
    from backend.app.tasks.worker_tasks import dispatch_review_tasks

    dispatch_review_tasks(submission.id, submission.household_id, attempt)


def _submission_view(submission):
    return {
        "submission_id": submission.id, "draft_id": submission.draft_id,
        "status": submission.status, "agent_version_id": submission.agent_version_id,
        "name": submission.name, "description": submission.description, "category": submission.category,
        "precheck_status": submission.precheck_status,
        "precheck": json.loads(submission.precheck_json or "{}") or {"passed": None, "codes": [], "checks": []},
        "risk_summary": {"status": submission.risk_summary_status,
                         **(json.loads(submission.risk_summary_json or "{}") or {})},
        "created_at": submission.created_at, "decided_at": submission.decided_at,
        "decision_reason": submission.decision_reason,
    }


@router.get("/submissions")
def list_submissions(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    rows = db.scalars(select(ReviewSubmission).where(
        ReviewSubmission.household_id == member.household_id).order_by(
        ReviewSubmission.created_at.desc(), ReviewSubmission.id.desc())).all()
    return [{"id": row.id, "draft_id": row.draft_id, "name": row.name, "status": row.status,
             "category": row.category, "precheck_status": row.precheck_status,
             "risk_summary_status": row.risk_summary_status,
             "created_at": row.created_at, "decision_reason": row.decision_reason} for row in rows]


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: str, account: Account = Depends(get_current_account),
                   db: Session = Depends(get_db)):
    member = _household(db, account.id)
    submission = db.scalar(select(ReviewSubmission).where(
        ReviewSubmission.id == submission_id,
        ReviewSubmission.household_id == member.household_id))
    if submission is None:
        raise HTTPException(404, detail={"code": "REVIEW_SUBMISSION_NOT_FOUND"})
    return _submission_view(submission)


@router.get("/agents")
def my_agents(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    rows = db.scalars(select(CreatorDraft).where(CreatorDraft.household_id == member.household_id,
                                                 CreatorDraft.status == "private_published")).all()
    result = []
    for draft in rows:
        if not draft.agent_definition_id:
            continue
        versions = db.scalars(select(AgentVersion).where(
            AgentVersion.agent_id == draft.agent_definition_id).order_by(AgentVersion.version)).all()
        card = json.loads(draft.intent_card_json or "{}")
        result.append({"draft_id": draft.id, "agent_id": draft.agent_definition_id,
                       "name": card.get("name", draft.title), "description": card.get("description", ""),
                       "deployment_id": draft.deployment_id, "published_at": draft.updated_at,
                       "versions": [{"id": row.id, "version": row.version, "published_at": row.published_at}
                                    for row in versions]})
    return result
