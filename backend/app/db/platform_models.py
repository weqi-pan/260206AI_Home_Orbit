from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer,
                        LargeBinary, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, utcnow, uuid_str


class LlmEndpoint(Base):
    """Platform-global administrator-managed model connection."""
    __tablename__ = "llm_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    disabled_reason: Mapped[str | None] = mapped_column(String(64))


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
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    use_responses_api: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capability_flags_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    requests_per_minute: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    agent_token_budget: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ModelPurposeBinding(Base):
    """Platform-global default purpose binding configured by administrators."""
    __tablename__ = "model_purpose_bindings"
    purpose: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"), nullable=False)
    config_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    endpoint_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    model_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ModelHealthCheck(Base):
    """Platform-global capability evidence for a model profile."""
    __tablename__ = "model_health_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    capabilities_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    endpoint_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    model_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolDefinition(Base):
    """Platform-global built-in Tool Catalog entry; users cannot add executable tools."""
    __tablename__ = "tool_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    catalog_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class TenantRow:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def tenant_parent_constraints(*constraints):
    return (*constraints, UniqueConstraint("id", "household_id"))


class ModelGrant(TenantRow, Base):
    __tablename__ = "model_grants"
    __table_args__ = (UniqueConstraint("household_id", "model_id", name="uq_model_grant_household_model"),)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"), nullable=False)


class UsageQuota(TenantRow, Base):
    __tablename__ = "usage_quotas"
    __table_args__ = (UniqueConstraint("household_id", name="uq_usage_quota_household"),)
    monthly_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    agent_token_budget: Mapped[int | None] = mapped_column(Integer)


class ModelUsage(Base):
    __tablename__ = "model_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EmbeddingCollectionBuild(Base):
    __tablename__ = "embedding_collection_builds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"), nullable=False)
    collection_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="building")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActiveEmbeddingCollection(Base):
    __tablename__ = "active_embedding_collection"
    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="knowledge")
    collection_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_id: Mapped[str] = mapped_column(ForeignKey("llm_models.id"), nullable=False)
    previous_collection_name: Mapped[str | None] = mapped_column(String(200))


class ToolPermission(TenantRow, Base):
    __tablename__ = "tool_permissions"
    __table_args__ = (UniqueConstraint("household_id", "tool_id", name="uq_tool_permission_household_tool"),)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    granted_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))


class Skill(TenantRow, Base):
    __tablename__ = "skills"
    __table_args__ = tenant_parent_constraints()
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = tenant_parent_constraints(
        UniqueConstraint("skill_id", "version"),
        ForeignKeyConstraint(["skill_id", "household_id"], ["skills.id", "skills.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime)


class SkillKnowledgeAsset(Base):
    __tablename__ = "skill_knowledge_assets"
    __table_args__ = (
        ForeignKeyConstraint(["skill_version_id", "household_id"], ["skill_versions.id", "skill_versions.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_version_id: Mapped[str] = mapped_column(String(36), nullable=False)


class AgentDefinition(TenantRow, Base):
    __tablename__ = "agent_definitions"
    __table_args__ = tenant_parent_constraints()
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = tenant_parent_constraints(
        UniqueConstraint("agent_id", "version"),
        ForeignKeyConstraint(["agent_id", "household_id"], ["agent_definitions.id", "agent_definitions.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime)


class AgentSkillBinding(Base):
    __tablename__ = "agent_skill_bindings"
    __table_args__ = (
        ForeignKeyConstraint(["agent_version_id", "household_id"], ["agent_versions.id", "agent_versions.household_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["skill_version_id", "household_id"], ["skill_versions.id", "skill_versions.household_id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    skill_version_id: Mapped[str] = mapped_column(String(36), nullable=False)


class AgentToolBinding(Base):
    __tablename__ = "agent_tool_bindings"
    __table_args__ = (
        ForeignKeyConstraint(["agent_version_id", "household_id"], ["agent_versions.id", "agent_versions.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)


class AgentDeployment(TenantRow, Base):
    __tablename__ = "agent_deployments"
    __table_args__ = tenant_parent_constraints(
        ForeignKeyConstraint(["agent_version_id", "household_id"], ["agent_versions.id", "agent_versions.household_id"]),
    )
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")


class AgentInstallation(TenantRow, Base):
    __tablename__ = "agent_installations"
    __table_args__ = tenant_parent_constraints(
        ForeignKeyConstraint(["deployment_id", "household_id"], ["agent_deployments.id", "agent_deployments.household_id"]),
        ForeignKeyConstraint(["agent_version_id", "household_id"], ["agent_versions.id", "agent_versions.household_id"]),
        UniqueConstraint("household_id", "deployment_id", "agent_version_id", name="uq_agent_installation_authority"),
        UniqueConstraint("household_id", "deployment_id", "authority_slot", name="uq_agent_installation_active_slot"),
    )
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="installed")
    authority_slot: Mapped[str | None] = mapped_column(String(16), nullable=True, default="active")


class InstallationPermission(Base):
    __tablename__ = "installation_permissions"
    __table_args__ = (
        ForeignKeyConstraint(["installation_id", "household_id"], ["agent_installations.id", "agent_installations.household_id"], ondelete="CASCADE"),
        UniqueConstraint("installation_id", "tool_id", name="uq_installation_permission_tool"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    granted_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Favorite(TenantRow, Base):
    __tablename__ = "favorites"
    __table_args__ = (
        ForeignKeyConstraint(["deployment_id", "household_id"], ["agent_deployments.id", "agent_deployments.household_id"]),
    )
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)


class CreatorDraft(TenantRow, Base):
    __tablename__ = "creator_drafts"
    draft_type: Mapped[str] = mapped_column(String(16), nullable=False)


class ReviewSubmission(Base):
    __tablename__ = "review_submissions"
    __table_args__ = tenant_parent_constraints(
        ForeignKeyConstraint(["agent_version_id", "household_id"], ["agent_versions.id", "agent_versions.household_id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)


class ReviewCheck(Base):
    __tablename__ = "review_checks"
    __table_args__ = (
        ForeignKeyConstraint(["submission_id", "household_id"], ["review_submissions.id", "review_submissions.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    submission_id: Mapped[str] = mapped_column(String(36), nullable=False)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        ForeignKeyConstraint(["submission_id", "household_id"], ["review_submissions.id", "review_submissions.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    submission_id: Mapped[str] = mapped_column(String(36), nullable=False)


class Report(TenantRow, Base):
    __tablename__ = "reports"
    __table_args__ = (
        ForeignKeyConstraint(["deployment_id", "household_id"], ["agent_deployments.id", "agent_deployments.household_id"]),
    )
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)


class AgentRun(TenantRow, Base):
    __tablename__ = "agent_runs"
    __table_args__ = tenant_parent_constraints(
        ForeignKeyConstraint(["deployment_id", "household_id"], ["agent_deployments.id", "agent_deployments.household_id"]),
    )
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    principal_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    member_id: Mapped[str] = mapped_column(ForeignKey("household_members.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deployment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_versions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    compiled_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    model_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    permission_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    consent_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    budget_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "household_id"], ["agent_runs.id", "agent_runs.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)
    principal_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    skill_versions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deployment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    redaction_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "household_id"], ["agent_runs.id", "agent_runs.household_id"], ondelete="CASCADE"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    principal_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool_definitions.id"), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    deployment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deployment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    approved_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    result_json: Mapped[str | None] = mapped_column(Text)
    checkpoint_ns: Mapped[str | None] = mapped_column(String(250))
    checkpoint_id: Mapped[str | None] = mapped_column(String(100))
    consent_digest: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", name="uq_agent_checkpoint_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(100), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(250), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(100))
    checkpoint_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentCheckpointWrite(Base):
    __tablename__ = "agent_checkpoint_writes"
    __table_args__ = (UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index", name="uq_agent_checkpoint_write"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    household_id: Mapped[str] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(100), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(250), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(100), nullable=False)
    task_id: Mapped[str] = mapped_column(String(100), nullable=False)
    task_path: Mapped[str] = mapped_column(String(250), nullable=False, default="")
    write_index: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    value_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class HouseholdTask(TenantRow, Base):
    __tablename__ = "household_tasks"
    __table_args__ = tenant_parent_constraints(
        CheckConstraint("status IN ('open', 'done', 'cancelled')", name="ck_household_tasks_status"),
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TaskSchedule(TenantRow, Base):
    """Timezone-aware reminder rule: fixed interval with explicit next-run and missed-run policy."""
    __tablename__ = "task_schedules"
    __table_args__ = (
        ForeignKeyConstraint(["task_id", "household_id"], ["household_tasks.id", "household_tasks.household_id"], ondelete="CASCADE"),
        CheckConstraint("missed_run_policy IN ('skip', 'catch_up_bounded')", name="ck_task_schedules_missed_policy"),
    )
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    missed_run_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="skip")
    catch_up_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))


class Notification(TenantRow, Base):
    __tablename__ = "notifications"
    __table_args__ = tenant_parent_constraints()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")


class NotificationRead(TenantRow, Base):
    __tablename__ = "notification_reads"
    __table_args__ = (
        ForeignKeyConstraint(["notification_id", "household_id"], ["notifications.id", "notifications.household_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["account_id", "household_id"], ["household_members.account_id", "household_members.household_id"]),
        UniqueConstraint("notification_id", "account_id", name="uq_notification_read_notification_account"),
    )
    notification_id: Mapped[str] = mapped_column(String(36), nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class CeleryTaskRecord(Base):
    """Platform-global async task record: idempotency, attempts, retry policy and audit."""
    __tablename__ = "celery_tasks"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'running', 'succeeded', 'failed', 'retry_scheduled', 'dead')",
                        name="ck_celery_tasks_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    household_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(200))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    audit_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
