from backend.app.api.creator.base import *
from backend.app.api.creator.base import _draft, _household, _trial_summary

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
