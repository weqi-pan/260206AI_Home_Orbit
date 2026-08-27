from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.models import Base, utcnow, uuid_str


class HouseholdResource:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LlmEndpoint(Base):
    __tablename__ = "llm_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class LlmCredential(Base):
    __tablename__ = "llm_credentials"
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("llm_endpoints.id", ondelete="CASCADE"), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)


class LlmModel(Base):
    __tablename__ = "llm_models"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("llm_endpoints.id", ondelete="CASCADE"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ModelPurposeBinding(Base):
    __tablename__ = "model_purpose_bindings"
    purpose: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"), nullable=False)


def household_table(name: str):
    return type(name.title().replace("_", ""), (HouseholdResource, Base), {"__tablename__": name})


ModelGrant = household_table("model_grants")
UsageQuota = household_table("usage_quotas")
ToolPermission = household_table("tool_permissions")
AgentInstallation = household_table("agent_installations")
InstallationPermission = household_table("installation_permissions")
Favorite = household_table("favorites")
CreatorDraft = household_table("creator_drafts")
Report = household_table("reports")
AgentRun = household_table("agent_runs")
ToolExecution = household_table("tool_executions")
ApprovalRequest = household_table("approval_requests")
HouseholdTask = household_table("household_tasks")
TaskSchedule = household_table("task_schedules")
Notification = household_table("notifications")
NotificationRead = household_table("notification_reads")


class ModelHealthCheck(Base):
    __tablename__ = "model_health_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)


class VersionedDefinition(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str | None] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class Skill(VersionedDefinition):
    __tablename__ = "skills"


class AgentDefinition(VersionedDefinition):
    __tablename__ = "agent_definitions"


class ImmutableVersion:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime)


class SkillVersion(ImmutableVersion, Base):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)


class AgentVersion(ImmutableVersion, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False)


class AgentDeployment(Base):
    __tablename__ = "agent_deployments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str | None] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"), nullable=False)


def relation_table(name: str):
    return type(name.title().replace("_", ""), (Base,), {
        "__tablename__": name,
        "id": mapped_column(String(36), primary_key=True, default=uuid_str),
    })


SkillKnowledgeAsset = relation_table("skill_knowledge_assets")
AgentSkillBinding = relation_table("agent_skill_bindings")
AgentToolBinding = relation_table("agent_tool_bindings")
ReviewSubmission = relation_table("review_submissions")
ReviewCheck = relation_table("review_checks")
ReviewDecision = relation_table("review_decisions")
