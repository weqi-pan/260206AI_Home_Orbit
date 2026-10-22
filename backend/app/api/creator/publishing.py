from backend.app.api.creator.base import *
from backend.app.api.creator.base import _draft, _guard_draft_not_locked_in_review, _household

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
