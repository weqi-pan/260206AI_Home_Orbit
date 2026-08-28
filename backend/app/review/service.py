# -*- coding: utf-8 -*-
"""Phase 7 review governance: submission lifecycle, admin decisions and
takedown/report handling.

State machine (submission): precheck_pending -> review_pending | precheck_failed;
review_pending -> approved | rejected | returned;
precheck_failed/review_pending/returned/rejected -> precheck_pending (re-review).

Draft mirror: submit -> review_pending; approve -> public_published;
reject/return -> draft; takedown -> deprecated. Reviewed versions are created
immutable at submit time and never mutated. The review LLM risk summary is
advisory only: no code path in this service lets model output change status.
"""
import json

from sqlalchemy import select

from backend.app.db.base import utcnow


class ReviewError(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


MARKET_CATEGORIES = ("生活", "烹饪", "家务", "设备", "知识", "效率", "其他")
SUBMIT_ALLOWED_DRAFT_STATUSES = {"validated"}
DECIDE_ALLOWED = {
    "approve": {"review_pending"},
    "reject": {"review_pending"},
    "return": {"review_pending", "precheck_failed"},
    "re-review": {"review_pending", "precheck_failed", "returned", "rejected"},
}


class ReviewGovernanceService:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------ submit
    def submit(self, draft, account_id, category="生活"):
        """Materializes an immutable version stack from the validated draft and
        creates the review submission (status precheck_pending)."""
        from backend.app.creator.validation import DraftValidationRejected, DraftValidator
        from backend.app.creator.publishing import materialize_stack
        from backend.app.db.models import ReviewSubmission

        if category not in MARKET_CATEGORIES:
            raise ReviewError("CATEGORY_INVALID", 422)
        if draft.status not in SUBMIT_ALLOWED_DRAFT_STATUSES:
            raise ReviewError("DRAFT_NOT_VALIDATED")
        if not draft.intent_card_json:
            raise ReviewError("DRAFT_NOT_VALIDATED")
        card = json.loads(draft.intent_card_json)
        validator = DraftValidator(self.session, draft.household_id)
        try:
            validated = validator.validate(card, draft_id=draft.id)
        except DraftValidationRejected as exc:
            raise ReviewError("SUBMIT_VALIDATION_FAILED", 422) from exc

        agent, agent_version, _skills, _assets, _tools = materialize_stack(
            self.session, draft.household_id, draft, validated)
        submission = ReviewSubmission(
            household_id=draft.household_id, agent_version_id=agent_version.id, draft_id=draft.id,
            name=card["name"][:100], description=card.get("description", "")[:500], category=category,
            status="precheck_pending", submitted_by_account_id=account_id)
        self.session.add(submission)
        self.session.flush()
        draft.status = "review_pending"
        draft.updated_at = utcnow()
        self.session.flush()
        return submission

    # ----------------------------------------------------------------- decide
    def decide(self, submission, action, reason, admin_account_id):
        from backend.app.db.models import ReviewDecision

        if action not in DECIDE_ALLOWED:
            raise ReviewError("DECISION_ACTION_INVALID", 422)
        if submission.status not in DECIDE_ALLOWED[action]:
            raise ReviewError("DECISION_STATE_CONFLICT")
        if action in {"approve", "reject"} and not (reason or "").strip():
            raise ReviewError("DECISION_REASON_REQUIRED", 422)

        self.session.add(ReviewDecision(household_id=submission.household_id,
                                        submission_id=submission.id, action=action,
                                        reason=(reason or "")[:500], decided_by_account_id=admin_account_id))
        submission.decided_by_account_id = admin_account_id
        submission.decided_at = utcnow()
        submission.decision_reason = (reason or "")[:500]

        if action == "approve":
            self._approve(submission)
        elif action == "reject":
            submission.status = "rejected"
            self._mirror_draft(submission, "draft")
        elif action == "return":
            submission.status = "returned"
            self._mirror_draft(submission, "draft")
        elif action == "re-review":
            submission.status = "precheck_pending"
            submission.precheck_status = "pending"
            submission.risk_summary_status = "pending"
            self._mirror_draft(submission, "review_pending")
        self.session.flush()
        return submission

    def _approve(self, submission):
        from backend.app.db.models import AgentVersion, MarketEntry

        version = self.session.get(AgentVersion, submission.agent_version_id)
        entry = self._entry_for(submission)
        if entry is not None and entry.status == "deprecated":
            raise ReviewError("ENTRY_DEPRECATED")
        if entry is None:
            entry = MarketEntry(source_household_id=submission.household_id,
                                agent_definition_id=version.agent_id)
        entry.name = submission.name
        entry.description = submission.description
        entry.category = submission.category
        entry.official = False
        entry.status = "published"
        entry.taken_down_at = None
        entry.takedown_reason = None
        if version.version > (entry.current_version_number or 0):
            entry.current_version_id = version.id
            entry.current_version_number = version.version
        self.session.add(entry)
        self.session.flush()
        submission.entry_id = entry.id
        submission.status = "approved"
        self._mirror_draft(submission, "public_published")

    def _entry_for(self, submission):
        from backend.app.db.models import AgentVersion, MarketEntry

        if submission.entry_id:
            return self.session.get(MarketEntry, submission.entry_id)
        version = self.session.get(AgentVersion, submission.agent_version_id)
        return self.session.scalar(select(MarketEntry).where(
            MarketEntry.source_household_id == submission.household_id,
            MarketEntry.agent_definition_id == version.agent_id).order_by(MarketEntry.created_at))

    def _mirror_draft(self, submission, status):
        draft = self.session.get(self._draft_model(), submission.draft_id)
        if draft is not None:
            draft.status = status
            draft.updated_at = utcnow()

    # --------------------------------------------------------------- takedown
    def takedown(self, entry, reason, admin_account_id):
        from backend.app.db.models import ReviewSubmission

        if entry.status != "published":
            raise ReviewError("ENTRY_NOT_PUBLISHED")
        if not (reason or "").strip():
            raise ReviewError("DECISION_REASON_REQUIRED", 422)
        entry.status = "deprecated"
        entry.taken_down_at = utcnow()
        entry.takedown_reason = reason[:500]
        approved = self.session.scalar(select(ReviewSubmission).where(
            ReviewSubmission.entry_id == entry.id,
            ReviewSubmission.status == "approved").order_by(ReviewSubmission.decided_at.desc(),
                                                            ReviewSubmission.id.desc()))
        if approved is not None:
            self._mirror_draft(approved, "deprecated")
        self.session.flush()
        return entry

    # ----------------------------------------------------------------- reports
    def handle_report(self, report, action, note, admin_account_id):
        if action not in {"resolve", "dismiss"}:
            raise ReviewError("REPORT_ACTION_INVALID", 422)
        if report.status != "open":
            raise ReviewError("REPORT_STATE_CONFLICT")
        report.status = "resolved" if action == "resolve" else "dismissed"
        report.handled_by_account_id = admin_account_id
        report.handled_at = utcnow()
        report.resolution_note = (note or "")[:500]
        self.session.flush()
        return report

    # ------------------------------------------------------------------ helpers
    def _draft_model(self):
        from backend.app.db.models import CreatorDraft

        return CreatorDraft

    def prior_public_version(self, submission):
        """Prior approved version of the same entry (version diff baseline)."""
        from backend.app.db.models import AgentVersion, MarketEntry, ReviewSubmission

        entry_id = submission.entry_id
        if entry_id is None:
            version = self.session.get(AgentVersion, submission.agent_version_id)
            entry = self.session.scalar(select(MarketEntry).where(
                MarketEntry.source_household_id == submission.household_id,
                MarketEntry.agent_definition_id == version.agent_id))
            entry_id = entry.id if entry else None
        if entry_id is None:
            return None
        rows = self.session.execute(select(ReviewSubmission, AgentVersion).join(
            AgentVersion, AgentVersion.id == ReviewSubmission.agent_version_id).where(
            ReviewSubmission.entry_id == entry_id, ReviewSubmission.status == "approved",
            ReviewSubmission.id != submission.id).order_by(AgentVersion.version.desc())).all()
        for _row, version in rows:
            return version
        return None
