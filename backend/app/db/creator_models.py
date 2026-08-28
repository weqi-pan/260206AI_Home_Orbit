"""Phase 6 creator domain tables: draft knowledge assets and sandbox trial reports."""
from datetime import datetime

from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, utcnow, uuid_str


class CreatorKnowledgeAsset(Base):
    """Household-scoped knowledge file attached to a draft skill.

    Files are scanned (type/size/content safety) before any vectorization; the
    scan verdict and reason are persisted so rejection evidence is auditable.
    """
    __tablename__ = "creator_knowledge_assets"
    __table_args__ = (
        ForeignKeyConstraint(["draft_id", "household_id"],
                             ["creator_drafts.id", "creator_drafts.household_id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('active', 'deleted')", name="ck_creator_knowledge_status"),
        CheckConstraint("scan_status IN ('pending', 'passed', 'rejected')", name="ck_creator_knowledge_scan"),
        UniqueConstraint("id", "household_id"),
        UniqueConstraint("draft_id", "household_id", "file_md5", name="uq_creator_asset_draft_md5"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_id: Mapped[str] = mapped_column(String(36), nullable=False)
    skill_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_md5: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    scan_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    scan_reason: Mapped[str | None] = mapped_column(String(300))
    uploaded_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CreatorTrialReport(Base):
    """Persisted sandbox trial report: per test case input, pass/fail, sanitized output."""
    __tablename__ = "creator_trial_reports"
    __table_args__ = (
        ForeignKeyConstraint(["draft_id", "household_id"],
                             ["creator_drafts.id", "creator_drafts.household_id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')",
                        name="ck_creator_trial_status"),
        UniqueConstraint("id", "household_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    granted_tool_keys_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    cases_json: Mapped[str | None] = mapped_column(Text)
    agent_version_id: Mapped[str | None] = mapped_column(String(36))
    deployment_id: Mapped[str | None] = mapped_column(String(36))
    installation_id: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
