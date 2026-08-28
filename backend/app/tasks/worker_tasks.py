"""Celery task bodies. Each body funnels into the broker-independent TaskExecutor
so worker, beat and manual trigger share idempotency/retry semantics."""
from backend.app.core.config import get_settings
from backend.app.db.session import create_database
from backend.app.tasks.executor import TaskExecutor
from backend.app.db.base import utcnow


def _database():
    return create_database(get_settings())


def _run(task_type, idempotency_key, payload=None, household_id=None):
    database = _database()
    try:
        executor = TaskExecutor(database.session_factory)
        executor.submit(task_type, payload or {}, idempotency_key, household_id=household_id)
        return executor.run(idempotency_key)
    finally:
        database.engine.dispose()


def reminders_dispatch():
    """Beat: fan reminder dispatch out per household for this tick."""
    database = _database()
    try:
        return TaskExecutor(database.session_factory).dispatch_all_households(
            "household.reminders.dispatch", key_prefix="beat:reminders")
    finally:
        database.engine.dispose()


def process_retries():
    database = _database()
    try:
        return TaskExecutor(database.session_factory).process_due_retries()
    finally:
        database.engine.dispose()


def models_health_check():
    stamp = utcnow().replace(microsecond=0).isoformat()
    return _run("models.health_check", f"beat:models-health-check:{stamp}")


def knowledge_vectorize(file_id):
    return _run("knowledge.vectorize", f"vectorize:{file_id}", {"file_id": file_id})


def faq_generate(file_id, household_id):
    return _run("content.faq_generate", f"faq-generate:{file_id}:{household_id}",
                {"file_id": file_id}, household_id=household_id)


def sandbox_trial(definition, household_id, draft_id):
    return _run("creator.sandbox_trial", f"sandbox-trial:{draft_id}",
                {"definition": definition}, household_id=household_id)


def sandbox_trial_run(draft_id, report_id, household_id, principal_id, granted_tool_keys):
    """Celery entrypoint used by the API in full-deployment mode."""
    payload = {"draft_id": draft_id, "report_id": report_id, "household_id": household_id,
               "principal_id": principal_id, "granted_tool_keys": granted_tool_keys}
    return _run("creator.sandbox_trial", f"sandbox-trial:{draft_id}:{report_id}", payload,
                household_id=household_id)


def dispatch_sandbox_trial(draft_id, report_id, household_id, principal_id, granted_tool_keys):
    """Submit the trial to the broker; never blocks the API request."""
    from backend.app.tasks.celery_app import create_celery_app

    create_celery_app().send_task("tasks.sandbox-trial-run", kwargs={
        "draft_id": draft_id, "report_id": report_id, "household_id": household_id,
        "principal_id": principal_id, "granted_tool_keys": granted_tool_keys})


def review_precheck_run(submission_id, household_id, attempt):
    return _run("review.precheck", f"review.precheck:{submission_id}:{attempt}",
                {"submission_id": submission_id}, household_id=household_id)


def review_risk_summary_run(submission_id, household_id, attempt):
    return _run("review.risk_summary", f"review.risk_summary:{submission_id}:{attempt}",
                {"submission_id": submission_id}, household_id=household_id)


def dispatch_review_tasks(submission_id, household_id, attempt):
    """Submit precheck + risk summary to the broker; never blocks the API request.

    The risk task retries transiently until the precheck task has committed.
    """
    from backend.app.tasks.celery_app import create_celery_app

    app = create_celery_app()
    app.send_task("tasks.review-precheck-run",
                  kwargs={"submission_id": submission_id, "household_id": household_id, "attempt": attempt})
    app.send_task("tasks.review-risk-summary-run",
                  kwargs={"submission_id": submission_id, "household_id": household_id, "attempt": attempt})


def register_tasks(app):
    app.task(name="tasks.reminders-dispatch")(reminders_dispatch)
    app.task(name="tasks.process-retries")(process_retries)
    app.task(name="tasks.models-health-check")(models_health_check)
    app.task(name="tasks.knowledge-vectorize")(knowledge_vectorize)
    app.task(name="tasks.faq-generate")(faq_generate)
    app.task(name="tasks.sandbox-trial")(sandbox_trial)
    app.task(name="tasks.sandbox-trial-run")(sandbox_trial_run)
    app.task(name="tasks.review-precheck-run")(review_precheck_run)
    app.task(name="tasks.review-risk-summary-run")(review_risk_summary_run)
    return app
