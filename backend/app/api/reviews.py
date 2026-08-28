# -*- coding: utf-8 -*-
"""Phase 7 admin review workbench API (/api/reviews/*).

Admin-only (require_admin) and audited via AdminActionAudit: queue with
filters, submission detail with the full evidence bundle (listing preview,
version diff against the prior public version, knowledge sources and scan
results, risk-labeled permissions, sandbox trajectory, pre-check results and
the advisory LLM risk summary) and the decisions approve/reject/return/
re-review plus entry takedown and report handling. The review LLM summary is
advisory: no endpoint or service path derives approval from it.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, AdminActionAudit, Report, ReviewSubmission
from backend.app.market.service import version_tools
from backend.app.review.risk import risk_payload
from backend.app.review.service import ReviewError, ReviewGovernanceService
from backend.app.schemas.market import (ReportHandleRequest, ReviewDecisionRequest, TakedownRequest)

router = APIRouter(prefix="/reviews", tags=["reviews"], dependencies=[Depends(require_admin)])


def _submission_or_404(db, submission_id) -> ReviewSubmission:
    submission = db.get(ReviewSubmission, submission_id)
    if submission is None:
        raise HTTPException(404, detail={"code": "REVIEW_SUBMISSION_NOT_FOUND"})
    return submission


def _audit(db, admin, action, resource_type, resource_id, detail):
    db.add(AdminActionAudit(principal_id=admin.id, household_id=detail.pop("household_id", None),
                            action=action, resource_type=resource_type, resource_id=str(resource_id),
                            detail_json=json.dumps(detail, ensure_ascii=False, sort_keys=True)))
    db.commit()


@router.get("/queue")
def queue(status: str | None = None, entry_id: str | None = None, limit: int = 100,
          _admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    from backend.app.db.models import AgentVersion, MarketEntry

    statement = select(ReviewSubmission, AgentVersion).join(
        AgentVersion, AgentVersion.id == ReviewSubmission.agent_version_id).order_by(
        ReviewSubmission.created_at.desc(), ReviewSubmission.id.desc()).limit(min(limit, 200))
    if status:
        statement = statement.where(ReviewSubmission.status == status)
    if entry_id:
        statement = statement.where(ReviewSubmission.entry_id == entry_id)
    items = []
    for submission, version in db.execute(statement).all():
        prior_number = None
        if submission.entry_id:
            entry = db.get(MarketEntry, submission.entry_id)
            if entry is not None and entry.current_version_id != submission.agent_version_id:
                prior = db.get(AgentVersion, entry.current_version_id)
                prior_number = prior.version if prior else None
        items.append({
            "submission_id": submission.id, "entry_id": submission.entry_id,
            "name": submission.name, "household_id": submission.household_id,
            "status": submission.status, "category": submission.category,
            "version": version.version, "prior_public_version": prior_number,
            "precheck_status": submission.precheck_status,
            "risk_summary_status": submission.risk_summary_status,
            "submitted_at": submission.created_at, "decided_at": submission.decided_at,
            "decision_reason": submission.decision_reason,
        })
    return {"items": items}


def _definition_diff(current: dict, prior: dict) -> dict:
    def tool_keys(definition):
        return {item.get("tool_key") for item in definition.get("permissions", [])}

    budgets_current, budgets_prior = current.get("budgets", {}), prior.get("budgets", {})
    return {
        "prompt_changed": current.get("prompt") != prior.get("prompt"),
        "tools_added": sorted(tool_keys(current) - tool_keys(prior)),
        "tools_removed": sorted(tool_keys(prior) - tool_keys(current)),
        "budgets_changed": {key for key in set(budgets_current) | set(budgets_prior)
                            if budgets_current.get(key) != budgets_prior.get(key)},
        "workflow_changed": current.get("workflow") != prior.get("workflow"),
        "skills_changed": (current.get("metadata", {}).get("description")
                           != prior.get("metadata", {}).get("description")),
    }


@router.get("/submissions/{submission_id}")
def submission_detail(submission_id: str, _admin: Account = Depends(require_admin),
                      db: Session = Depends(get_db)):
    from backend.app.creator.knowledge import CreatorKnowledgeService  # noqa: F401 (scan evidence via rows)
    from backend.app.db.models import (AgentSkillBinding, AgentVersion, CreatorDraft, CreatorKnowledgeAsset,
                                       CreatorTrialReport, SkillVersion)

    submission = _submission_or_404(db, submission_id)
    governance = ReviewGovernanceService(db)
    version = db.get(AgentVersion, submission.agent_version_id)
    prior_version = governance.prior_public_version(submission)
    prior_definition = json.loads(prior_version.definition_json) if prior_version else None
    definition = json.loads(version.definition_json or "{}")

    knowledge = []
    skill_rows = db.execute(select(SkillVersion).join(
        AgentSkillBinding, AgentSkillBinding.skill_version_id == SkillVersion.id).where(
        AgentSkillBinding.agent_version_id == version.id,
        AgentSkillBinding.household_id == submission.household_id).order_by(AgentSkillBinding.id)).all()
    for (skill,) in skill_rows:
        for attachment in json.loads(skill.definition_json or "{}").get("attachments", []):
            if attachment.get("kind") != "knowledge":
                continue
            asset = db.get(CreatorKnowledgeAsset, attachment.get("asset_id"))
            knowledge.append({"asset_id": attachment.get("asset_id"),
                              "filename": asset.filename if asset else attachment.get("name"),
                              "file_type": asset.file_type if asset else "",
                              "scan_status": asset.scan_status if asset else "missing",
                              "scan_reason": asset.scan_reason if asset else None,
                              "content_hash": attachment.get("content_hash")})

    trial = db.scalar(select(CreatorTrialReport).where(
        CreatorTrialReport.household_id == submission.household_id,
        CreatorTrialReport.draft_id == submission.draft_id).order_by(
        CreatorTrialReport.created_at.desc(), CreatorTrialReport.id.desc()))

    return {
        "submission": {
            "id": submission.id, "status": submission.status, "household_id": submission.household_id,
            "draft_id": submission.draft_id, "category": submission.category,
            "precheck_status": submission.precheck_status,
            "risk_summary_status": submission.risk_summary_status,
            "submitted_at": submission.created_at, "decided_at": submission.decided_at,
            "decision_reason": submission.decision_reason,
        },
        "listing_preview": {"name": submission.name, "description": submission.description,
                            "category": submission.category},
        "agent_version": {"id": version.id, "version": version.version, "definition": definition},
        "prior_public_version": ({"version": prior_version.version,
                                  "definition": prior_definition} if prior_version else None),
        "diff": _definition_diff(definition, prior_definition) if prior_definition else None,
        "permissions": version_tools(db, submission.household_id, version.id),
        "knowledge": knowledge,
        "sandbox": ({"status": trial.status, "error_code": trial.error_code,
                     "cases": json.loads(trial.cases_json or "[]")} if trial else None),
        "precheck": json.loads(submission.precheck_json or "{}") or None,
        "risk_summary": {"status": submission.risk_summary_status,
                         **(json.loads(submission.risk_summary_json or "{}") or {})},
        "risk_payload": risk_payload(db, submission),
    }


@router.post("/submissions/{submission_id}/decision")
def decide(submission_id: str, payload: ReviewDecisionRequest, request: Request,
           admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    submission = _submission_or_404(db, submission_id)
    governance = ReviewGovernanceService(db)
    try:
        governance.decide(submission, payload.action, payload.reason, admin.id)
    except ReviewError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    db.commit()
    _dispatch_followup(request, submission)
    _audit(db, admin, f"review.{payload.action}", "review_submission", submission.id,
           {"household_id": submission.household_id, "reason": payload.reason,
            "action": payload.action})
    return {"submission_id": submission.id, "status": submission.status}


def _dispatch_followup(request, submission):
    """Re-review re-runs the precheck + risk summary tasks (idempotent)."""
    if submission.status != "precheck_pending":
        return
    attempt = submission.precheck_attempt + 1
    settings = request.app.state.settings
    if settings.app_mode != "production":
        from backend.app.tasks.executor import TaskExecutor

        executor = TaskExecutor(request.app.state.database.session_factory)
        executor.submit("review.precheck", {"submission_id": submission.id},
                        f"review.precheck:{submission.id}:{attempt}", household_id=submission.household_id)
        executor.run(f"review.precheck:{submission.id}:{attempt}")
        db = request.app.state.database.session_factory()
        try:
            fresh = db.get(ReviewSubmission, submission.id)
            if fresh.status == "review_pending":
                executor.submit("review.risk_summary", {"submission_id": submission.id},
                                f"review.risk_summary:{submission.id}:{attempt}",
                                household_id=submission.household_id)
                executor.run(f"review.risk_summary:{submission.id}:{attempt}")
                db.refresh(fresh)
                submission.status = fresh.status
        finally:
            db.close()
        return
    from backend.app.tasks.worker_tasks import dispatch_review_tasks

    dispatch_review_tasks(submission.id, submission.household_id, attempt)


@router.post("/entries/{entry_id}/takedown")
def takedown(entry_id: str, payload: TakedownRequest, admin: Account = Depends(require_admin),
             db: Session = Depends(get_db)):
    from backend.app.db.models import MarketEntry

    entry = db.get(MarketEntry, entry_id)
    if entry is None:
        raise HTTPException(404, detail={"code": "MARKET_ENTRY_NOT_FOUND"})
    governance = ReviewGovernanceService(db)
    try:
        governance.takedown(entry, payload.reason, admin.id)
    except ReviewError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    db.commit()
    _audit(db, admin, "review.takedown", "market_entry", entry.id,
           {"household_id": entry.source_household_id, "reason": payload.reason})
    return {"entry_id": entry.id, "status": entry.status}


@router.get("/reports")
def reports(status: str | None = None, _admin: Account = Depends(require_admin),
            db: Session = Depends(get_db)):
    from backend.app.db.models import MarketEntry

    statement = select(Report, MarketEntry).join(
        MarketEntry, MarketEntry.id == Report.entry_id, isouter=True).order_by(
        Report.created_at.desc(), Report.id.desc())
    if status:
        statement = statement.where(Report.status == status)
    items = []
    for report, entry in db.execute(statement).all():
        items.append({"id": report.id, "entry_id": report.entry_id,
                      "entry_name": entry.name if entry else None,
                      "entry_status": entry.status if entry else None,
                      "household_id": report.household_id,
                      "reporter_account_id": report.reporter_account_id,
                      "reason": report.reason, "status": report.status,
                      "resolution_note": report.resolution_note, "created_at": report.created_at})
    return {"items": items}


@router.post("/reports/{report_id}/handle")
def handle_report(report_id: str, payload: ReportHandleRequest, admin: Account = Depends(require_admin),
                  db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, detail={"code": "REPORT_NOT_FOUND"})
    governance = ReviewGovernanceService(db)
    try:
        governance.handle_report(report, payload.action, payload.note, admin.id)
    except ReviewError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    db.commit()
    _audit(db, admin, f"report.{payload.action}", "market_report", report.id,
           {"household_id": report.household_id, "note": payload.note})
    return {"id": report.id, "status": report.status}
