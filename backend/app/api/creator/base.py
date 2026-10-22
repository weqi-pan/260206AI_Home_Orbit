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
