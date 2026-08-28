"""Phase 7 market/review governance tables.

Public publishing lifecycle evidence: review submissions carry the automatic
pre-check result and the advisory-only LLM risk summary; decisions audit every
admin action; market entries are the public listing (immutable reviewed
versions, install counter, takedown state); market installs pin household
installations to the reviewed version they installed.
"""
from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, utcnow, uuid_str
from backend.app.db.platform_models import TenantRow

SUBMISSION_STATUSES = ("precheck_pending", "precheck_failed", "review_pending", "returned", "rejected",
                       "approved")


class MarketEntry(Base):
    """Platform-scoped public marketplace listing for a creator Agent."""
    __tablename__ = "market_entries"
    __table_args__ = (
        CheckConstraint("status IN ('published', 'deprecated')", name="ck_market_entries_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"),
                                                     nullable=False, index=True)
    agent_definition_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="生活")
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    install_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    taken_down_at: Mapped[datetime | None] = mapped_column(DateTime)
    takedown_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MarketInstall(Base):
    """Household-scoped record pinning an installation to the reviewed market version."""
    __tablename__ = "market_installs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("market_entries.id", ondelete="CASCADE"), nullable=False,
                                          index=True)
    installation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    pinned_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    installed_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    upgraded_at: Mapped[datetime | None] = mapped_column(DateTime)


class ReviewSubmission(Base):
    """Creator household's public-review submission for one immutable agent version."""
    __tablename__ = "review_submissions"
    __table_args__ = (
        ForeignKeyConstraint(["agent_version_id", "household_id"],
                             ["agent_versions.id", "agent_versions.household_id"]),
        CheckConstraint("status IN ('precheck_pending', 'precheck_failed', 'review_pending', 'returned',"
                        " 'rejected', 'approved')", name="ck_review_submissions_status"),
        CheckConstraint("precheck_status IN ('pending', 'passed', 'failed')",
                        name="ck_review_submissions_precheck"),
        CheckConstraint("risk_summary_status IN ('pending', 'done', 'unavailable')",
                        name="ck_review_submissions_risk_summary"),
        UniqueConstraint("id", "household_id"),
        Index("ix_review_submissions_status_created", "status", "created_at"),
        Index("ix_review_submissions_entry_id", "entry_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    draft_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="生活")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="precheck_pending")
    submitted_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    precheck_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    precheck_json: Mapped[str | None] = mapped_column(Text)
    precheck_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    precheck_finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    risk_summary_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    risk_summary_json: Mapped[str | None] = mapped_column(Text)
    entry_id: Mapped[str | None] = mapped_column(ForeignKey("market_entries.id"))
    decided_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ReviewCheck(Base):
    """Persisted automatic pre-check evidence for one submission precheck run."""
    __tablename__ = "review_checks"
    __table_args__ = (
        ForeignKeyConstraint(["submission_id", "household_id"],
                             ["review_submissions.id", "review_submissions.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    submission_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    codes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReviewDecision(Base):
    """Audited admin decision: approve / reject / return / re-review."""
    __tablename__ = "review_decisions"
    __table_args__ = (
        ForeignKeyConstraint(["submission_id", "household_id"],
                             ["review_submissions.id", "review_submissions.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
                                              index=True)
    submission_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    decided_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Favorite(TenantRow, Base):
    """Household favorite of a marketplace entry."""
    __tablename__ = "favorites"
    __table_args__ = (
        ForeignKeyConstraint(["deployment_id", "household_id"],
                             ["agent_deployments.id", "agent_deployments.household_id"]),
    )
    deployment_id: Mapped[str | None] = mapped_column(String(36))
    entry_id: Mapped[str | None] = mapped_column(ForeignKey("market_entries.id"))


class Report(TenantRow, Base):
    """User report against a marketplace entry; admin resolves or dismisses."""
    __tablename__ = "reports"
    __table_args__ = (
        ForeignKeyConstraint(["deployment_id", "household_id"],
                             ["agent_deployments.id", "agent_deployments.household_id"]),
        CheckConstraint("status IN ('open', 'resolved', 'dismissed')", name="ck_reports_status"),
    )
    deployment_id: Mapped[str | None] = mapped_column(String(36))
    entry_id: Mapped[str | None] = mapped_column(ForeignKey("market_entries.id"), index=True)
    reporter_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    handled_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_note: Mapped[str | None] = mapped_column(String(500))
