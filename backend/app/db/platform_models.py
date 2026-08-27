from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.models import Base, utcnow, uuid_str


class LlmEndpoint(Base):
    """Platform-global administrator-managed model connection."""
    __tablename__ = "llm_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class LlmCredential(Base):
    """Platform-global encrypted credential owned by an endpoint."""
    __tablename__ = "llm_credentials"
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("llm_endpoints.id", ondelete="CASCADE"), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)


class LlmModel(Base):
    """Platform-global model profile available for household grants."""
    __tablename__ = "llm_models"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("llm_endpoints.id", ondelete="CASCADE"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ModelPurposeBinding(Base):
    """Platform-global default purpose binding configured by administrators."""
    __tablename__ = "model_purpose_bindings"
    purpose: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"), nullable=False)


class ModelHealthCheck(Base):
    """Platform-global capability evidence for a model profile."""
    __tablename__ = "model_health_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolDefinition(Base):
    """Platform-global built-in Tool Catalog entry; users cannot add executable tools."""
    __tablename__ = "tool_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)


class TenantRow:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelGrant(TenantRow, Base):
    __tablename__ = "model_grants"
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"), nullable=False)


class UsageQuota(TenantRow, Base):
    __tablename__ = "usage_quotas"
    monthly_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ToolPermission(TenantRow, Base):
    __tablename__ = "tool_permissions"
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)


class Skill(TenantRow, Base):
    __tablename__ = "skills"
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime)


class SkillKnowledgeAsset(Base):
    __tablename__ = "skill_knowledge_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    skill_version_id: Mapped[str] = mapped_column(ForeignKey("skill_versions.id", ondelete="CASCADE"), nullable=False)


class AgentDefinition(TenantRow, Base):
    __tablename__ = "agent_definitions"
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime)


class AgentSkillBinding(Base):
    __tablename__ = "agent_skill_bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False)
    skill_version_id: Mapped[str] = mapped_column(ForeignKey("skill_versions.id"), nullable=False)


class AgentToolBinding(Base):
    __tablename__ = "agent_tool_bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)


class AgentDeployment(TenantRow, Base):
    __tablename__ = "agent_deployments"
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"), nullable=False)


class AgentInstallation(TenantRow, Base):
    __tablename__ = "agent_installations"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("agent_deployments.id"), nullable=False)


class InstallationPermission(Base):
    __tablename__ = "installation_permissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    installation_id: Mapped[str] = mapped_column(ForeignKey("agent_installations.id", ondelete="CASCADE"), nullable=False)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)


class Favorite(TenantRow, Base):
    __tablename__ = "favorites"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("agent_deployments.id"), nullable=False)


class CreatorDraft(TenantRow, Base):
    __tablename__ = "creator_drafts"
    draft_type: Mapped[str] = mapped_column(String(16), nullable=False)


class ReviewSubmission(Base):
    __tablename__ = "review_submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"), nullable=False)


class ReviewCheck(Base):
    __tablename__ = "review_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    submission_id: Mapped[str] = mapped_column(ForeignKey("review_submissions.id", ondelete="CASCADE"), nullable=False)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    submission_id: Mapped[str] = mapped_column(ForeignKey("review_submissions.id", ondelete="CASCADE"), nullable=False)


class Report(TenantRow, Base):
    __tablename__ = "reports"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("agent_deployments.id"), nullable=False)


class AgentRun(TenantRow, Base):
    __tablename__ = "agent_runs"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("agent_deployments.id"), nullable=False)


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)


class HouseholdTask(TenantRow, Base):
    __tablename__ = "household_tasks"
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class TaskSchedule(Base):
    __tablename__ = "task_schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("household_tasks.id", ondelete="CASCADE"), nullable=False)


class Notification(TenantRow, Base):
    __tablename__ = "notifications"
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    notification_id: Mapped[str] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
