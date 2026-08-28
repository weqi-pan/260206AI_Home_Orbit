from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, utcnow, uuid_str


class ShoppingListItem(Base):
    """Household-scoped shopping list entry; writes are reversible list edits."""
    __tablename__ = "shopping_list_items"
    __table_args__ = (
        ForeignKeyConstraint(["created_by_account_id"], ["accounts.id"]),
        UniqueConstraint("id", "household_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[float] = mapped_column(Integer, nullable=False, default=1)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="份")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_account_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HouseholdMemo(Base):
    """Household-scoped reversible memo."""
    __tablename__ = "household_memos"
    __table_args__ = (UniqueConstraint("id", "household_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_account_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HouseholdPreferenceProposal(Base):
    """Agent-proposed structured preference; only member confirmation writes the preference."""
    __tablename__ = "household_preference_proposals"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="ck_preference_proposals_status"),
        CheckConstraint("value_type IN ('string', 'number', 'boolean', 'json')", name="ck_preference_proposals_type"),
        CheckConstraint("proposed_by_kind IN ('agent', 'member')", name="ck_preference_proposals_kind"),
        UniqueConstraint("id", "household_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="string")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_by_account_id: Mapped[str | None] = mapped_column(String(36))
    proposed_by_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="agent")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    confirmed_by_account_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
