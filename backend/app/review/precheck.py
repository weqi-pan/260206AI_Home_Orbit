# -*- coding: utf-8 -*-
"""Phase 7 automatic review pre-check.

The complete pre-check runs before a submission can enter the human review
queue and is persisted as review evidence (ReviewCheck): strict schemas,
household permission ceiling, workflow validation, sensitive/forbidden
content, attachment scan status, prompt-injection scan, budget sanity and a
passing sandbox trial report. Every failure maps to a stable code; the
submission is rejected (precheck_failed) rather than queued.
"""
import json

from sqlalchemy import select

from backend.app.db.base import utcnow

# 检查类别 -> DraftValidationRejected 错误码映射（无对应错误的类别由试运行/独立检查覆盖）
CATEGORY_CODE_MAP = {
    "INTENT_CARD_INVALID": "SCHEMA_STRICT",
    "INTENT_CARD_UNSAFE": "SENSITIVE_CONTENT",
    "AGENT_DEFINITION_INVALID": "SCHEMA_STRICT",
    "AGENT_DEFINITION_UNSAFE": "SENSITIVE_CONTENT",
    "SKILL_DEFINITION_INVALID": "SCHEMA_STRICT",
    "SKILL_DEFINITION_UNSAFE": "SENSITIVE_CONTENT",
    "TOOL_NOT_IN_CATALOG": "PERMISSION_CEILING",
    "TOOL_NOT_ENABLED": "PERMISSION_CEILING",
    "TOOL_CATALOG_MISMATCH": "PERMISSION_CEILING",
    "TOOL_NOT_GRANTED": "PERMISSION_CEILING",
    "KNOWLEDGE_ASSET_INVALID": "ATTACHMENT_SCAN",
    "PROMPT_INJECTION_DETECTED": "PROMPT_INJECTION",
    "BUDGET_INVALID": "BUDGET_SANITY",
}
WORKFLOW_CODES = {"WORKFLOW_INVALID", "WORKFLOW_NODE_INVALID", "WORKFLOW_CYCLE", "WORKFLOW_LIMIT",
                  "WORKFLOW_TOOL_UNKNOWN"}
PRECHECK_CATEGORIES = ("SCHEMA_STRICT", "PERMISSION_CEILING", "WORKFLOW_VALID", "SENSITIVE_CONTENT",
                       "ATTACHMENT_SCAN", "PROMPT_INJECTION", "BUDGET_SANITY", "SANDBOX_TRIAL")


class PrecheckError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _category_for(code: str) -> str:
    if code in CATEGORY_CODE_MAP:
        return CATEGORY_CODE_MAP[code]
    if code in WORKFLOW_CODES:
        return "WORKFLOW_VALID"
    return "SCHEMA_STRICT"


class ReviewPrecheckService:
    """Runs the complete automatic pre-check for one submission and persists evidence."""

    def __init__(self, session):
        self.session = session

    def run(self, submission) -> dict:
        """Executes all checks; does NOT commit (caller owns the transaction).

        Returns {"passed": bool, "codes": [str], "checks": [{code, passed, detail}]}.
        """
        from backend.app.creator.validation import DraftValidationRejected, DraftValidator
        from backend.app.db.models import CreatorDraft, CreatorTrialReport, ReviewCheck

        draft = self.session.get(CreatorDraft, submission.draft_id)
        checks = {category: {"code": category, "passed": True, "detail": ""}
                  for category in PRECHECK_CATEGORIES}
        if draft is None or draft.household_id != submission.household_id or not draft.intent_card_json:
            checks["SCHEMA_STRICT"] = {"code": "SCHEMA_STRICT", "passed": False, "detail": "DRAFT_EMPTY"}
        else:
            validator = DraftValidator(self.session, submission.household_id)
            try:
                validator.validate(json.loads(draft.intent_card_json), draft_id=draft.id)
            except DraftValidationRejected as exc:
                for error in exc.errors:
                    category = _category_for(error["code"])
                    checks[category]["passed"] = False
                    checks[category]["detail"] = checks[category]["detail"] or error["code"]

        trial = self._latest_trial(submission)
        if trial is None:
            checks["SANDBOX_TRIAL"] = {"code": "SANDBOX_TRIAL", "passed": False,
                                       "detail": "SANDBOX_TRIAL_REQUIRED"}
        elif trial.status != "completed":
            checks["SANDBOX_TRIAL"] = {"code": "SANDBOX_TRIAL", "passed": False, "detail": trial.error_code
                                       or "SANDBOX_TRIAL_NOT_COMPLETED"}
        else:
            cases = json.loads(trial.cases_json or "[]")
            failed = [case for case in cases if case.get("status") != "passed"]
            if not cases or failed:
                checks["SANDBOX_TRIAL"] = {
                    "code": "SANDBOX_TRIAL", "passed": False,
                    "detail": failed[0].get("error_code") if failed else "SANDBOX_TRIAL_NO_CASES"}
            elif self._trial_stale(trial, submission):
                checks["SANDBOX_TRIAL"] = {"code": "SANDBOX_TRIAL", "passed": False,
                                           "detail": "SANDBOX_TRIAL_STALE"}

        check_list = [checks[category] for category in PRECHECK_CATEGORIES]
        passed = all(item["passed"] for item in check_list)
        codes = [item["code"] for item in check_list if not item["passed"]]
        evidence = {"passed": passed, "codes": codes, "checks": check_list,
                    "agent_version_id": submission.agent_version_id}

        submission.precheck_attempt += 1
        submission.precheck_status = "passed" if passed else "failed"
        submission.precheck_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        submission.precheck_finished_at = utcnow()
        self.session.add(ReviewCheck(household_id=submission.household_id,
                                     submission_id=submission.id,
                                     attempt=submission.precheck_attempt, passed=passed,
                                     codes_json=json.dumps(codes, ensure_ascii=False),
                                     evidence_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True)))
        if passed:
            submission.status = "review_pending"
        else:
            submission.status = "precheck_failed"
        return evidence

    def _latest_trial(self, submission):
        from backend.app.db.models import CreatorTrialReport

        return self.session.scalar(select(CreatorTrialReport).where(
            CreatorTrialReport.household_id == submission.household_id,
            CreatorTrialReport.draft_id == submission.draft_id).order_by(
            CreatorTrialReport.created_at.desc(), CreatorTrialReport.id.desc()))

    def _trial_stale(self, trial, submission) -> bool:
        """试运行证据必须对应提交时的不可变定义；草稿编辑后必须重新试运行。"""
        from backend.app.db.models import AgentVersion

        if not trial.agent_version_id:
            return True
        trial_version = self.session.get(AgentVersion, trial.agent_version_id)
        submitted_version = self.session.get(AgentVersion, submission.agent_version_id)
        if trial_version is None or submitted_version is None:
            return True
        return trial_version.definition_json != submitted_version.definition_json
