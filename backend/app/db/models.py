from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, utcnow, uuid_str


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="上海")
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_accounts_role"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_accounts_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User | None] = relationship()


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        ForeignKeyConstraint(["deployment_id", "household_id"],
                             ["agent_deployments.id", "agent_deployments.household_id"]),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    deployment_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False, default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("priority IN ('low', 'normal', 'high')", name="ck_tickets_priority"),
        CheckConstraint("status IN ('open', 'processing', 'resolved', 'closed')", name="ck_tickets_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("chat_sessions.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emotion: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    admin_reply: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class KnowledgeFile(Base):
    __tablename__ = "knowledge_files"
    __table_args__ = (CheckConstraint("status IN ('active', 'deleted')", name="ck_knowledge_files_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_md5: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FaqPair(Base):
    __tablename__ = "faq_pairs"
    __table_args__ = (CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_faq_pairs_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_files.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Device(Base):
    __tablename__ = "devices"

    model_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    specs: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    features: Mapped[str] = mapped_column(Text, nullable=False)


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("model_id", "warehouse"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("devices.model_id"), nullable=False)
    warehouse: Mapped[str] = mapped_column(String(100), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RestockNotification(Base):
    __tablename__ = "restock_notifications"
    __table_args__ = (
        CheckConstraint("status IN ('waiting', 'notified')", name="ck_restock_notifications_status"),
        UniqueConstraint("account_id", "model_id", name="uq_restock_account_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    model_id: Mapped[str] = mapped_column(ForeignKey("devices.model_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="waiting")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeviceUsageRecord(Base):
    __tablename__ = "device_usage_records"
    __table_args__ = (UniqueConstraint("user_id", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    month: Mapped[str] = mapped_column(String(16), nullable=False)
    feature: Mapped[str] = mapped_column(Text, nullable=False)
    efficiency: Mapped[str] = mapped_column(Text, nullable=False)
    consumables: Mapped[str] = mapped_column(Text, nullable=False)
    comparison: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConversationHistory(Base):
    __tablename__ = "conversation_history"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_conversation_history_role"),
        CheckConstraint("feedback IS NULL OR feedback IN ('like', 'dislike')", name="ck_conversation_history_feedback"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_id"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment: Mapped[str | None] = mapped_column(String(32))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    feedback: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# Staged V2 compatibility exports. New domains live in focused modules while
# existing V1 imports continue to use backend.app.db.models.
from backend.app.db.household_models import Household, HouseholdMember, HouseholdPreference, Invitation  # noqa: E402,F401
from backend.app.db.platform_models import (  # noqa: E402,F401
    ActiveEmbeddingCollection, AgentCheckpoint, AgentCheckpointWrite, AgentDefinition, AgentDeployment, AgentInstallation,
    AgentRun, AgentSkillBinding, AgentToolBinding, AgentVersion, ApprovalRequest,
    EmbeddingCollectionBuild, LlmCredential, LlmEndpoint, LlmModel, ModelGrant,
    InstallationPermission, ModelHealthCheck, ModelPurposeBinding, ModelUsage, Notification, Skill, SkillVersion,
    ToolDefinition, ToolExecution, ToolPermission, UsageQuota,
)
