# -*- coding: utf-8 -*-
"""Phase 7 automatic review pre-check.

The complete pre-check runs before a submission can enter the human review
queue and is persisted as review evidence (ReviewCheck). The evidence ALWAYS
describes the frozen immutable artifact created at submit time
(``submission.agent_version_id`` → its AgentVersion definition, skill
bindings/definitions, tool bindings and knowledge attachments) — never the
live draft card, which may keep evolving in production async mode before the
task runs. Checks: strict schemas, sensitive/forbidden content,
prompt-injection scan, budget sanity, workflow validation, household
permission ceiling over the frozen tool set, attachment scan status and a
passing (non-stale) sandbox trial report. Every failure maps to a stable
code; the submission is rejected (precheck_failed) rather than queued, and
the draft mirror returns to ``validated`` so the creator can fix and
resubmit.
"""
import json

from sqlalchemy import select

from backend.app.db.base import utcnow

# 检查类别（与审核工作台展示一一对应）
PRECHECK_CATEGORIES = ("SCHEMA_STRICT", "PERMISSION_CEILING", "WORKFLOW_VALID", "SENSITIVE_CONTENT",
                       "ATTACHMENT_SCAN", "PROMPT_INJECTION", "BUDGET_SANITY", "SANDBOX_TRIAL")


def _fail(checks, category, detail):
    checks[category]["passed"] = False
    if not checks[category]["detail"]:
        checks[category]["detail"] = detail


class ReviewPrecheckService:
    """Runs the complete automatic pre-check for one submission and persists evidence."""

    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------ public
    def run(self, submission) -> dict:
        """Executes all checks against the frozen artifact; does NOT commit.

        Returns {"passed": bool, "codes": [str], "checks": [{code, passed, detail}]}.
        """
        from backend.app.db.models import CreatorDraft, ReviewCheck

        checks = {category: {"code": category, "passed": True, "detail": ""}
                  for category in PRECHECK_CATEGORIES}
        artifact = self._load_artifact(submission)
        if artifact is None:
            _fail(checks, "SCHEMA_STRICT", "SUBMISSION_ARTIFACT_MISSING")
        else:
            self._check_schema(artifact, checks)
            self._check_sensitive_content(artifact, checks)
            self._check_prompt_injection(artifact, checks)
            self._check_budgets(artifact, checks)
            self._check_workflow(artifact, checks)
            self._check_permission_ceiling(submission, artifact, checks)
            self._check_attachments(submission, artifact, checks)
        self._check_sandbox_trial(submission, checks)

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
            # 草稿镜像：预检失败回到 validated（内容可修复后重新提交；窗口期编辑锁随
            # submission 离开 precheck_pending/review_pending 自动解除）
            draft = self.session.get(CreatorDraft, submission.draft_id)
            if draft is not None and draft.status == "review_pending":
                draft.status = "validated"
        return evidence

    # ---------------------------------------------------------------- artifact
    def _load_artifact(self, submission):
        """加载提交时固化的不可变工件：AgentVersion 定义 + 技能绑定/定义。"""
        from backend.app.db.models import AgentSkillBinding, AgentVersion, SkillVersion

        version = self.session.get(AgentVersion, submission.agent_version_id)
        if version is None or version.household_id != submission.household_id:
            return None
        skills = self.session.scalars(select(SkillVersion).join(
            AgentSkillBinding, AgentSkillBinding.skill_version_id == SkillVersion.id).where(
            AgentSkillBinding.agent_version_id == version.id,
            AgentSkillBinding.household_id == submission.household_id).order_by(
            AgentSkillBinding.id)).all()
        return {"version": version, "definition": json.loads(version.definition_json or "{}"),
                "skills": skills}

    def _iter_text(self, artifact):
        """冻结工件的全部可扫描文本（提示词/测试输入/技能指令/描述）。"""
        definition = artifact["definition"]
        yield "prompt", definition.get("prompt") or ""
        for index, case in enumerate(definition.get("tests") or []):
            yield f"tests[{index}].input", (case or {}).get("input") or ""
        metadata = definition.get("metadata") or {}
        if metadata.get("description"):
            yield "metadata.description", metadata["description"]
        for index, skill in enumerate(artifact["skills"]):
            skill_definition = json.loads(skill.definition_json or "{}")
            yield f"skills[{index}].instructions", skill_definition.get("instructions") or ""

    def _skill_definitions(self, artifact):
        return [json.loads(skill.definition_json or "{}") for skill in artifact["skills"]]

    # ------------------------------------------------------------------ checks
    def _check_schema(self, artifact, checks):
        from backend.agent.platform.versions import (DefinitionRejected, validate_agent_definition,
                                                     validate_skill_definition)

        try:
            validate_agent_definition(artifact["definition"])
        except DefinitionRejected as exc:
            _fail(checks, "SENSITIVE_CONTENT" if exc.code.endswith("_UNSAFE") else "SCHEMA_STRICT",
                  exc.code)
        for skill_definition in self._skill_definitions(artifact):
            try:
                validate_skill_definition(skill_definition)
            except DefinitionRejected as exc:
                _fail(checks, "SENSITIVE_CONTENT" if exc.code.endswith("_UNSAFE") else "SCHEMA_STRICT",
                      exc.code)

    def _check_sensitive_content(self, artifact, checks):
        from backend.agent.platform.versions import UNSAFE_CONTENT

        for path, text in self._iter_text(artifact):
            if text and UNSAFE_CONTENT.search(text):
                _fail(checks, "SENSITIVE_CONTENT", path)
                return

    def _check_prompt_injection(self, artifact, checks):
        from backend.app.creator.validation import scan_injection

        for path, text in self._iter_text(artifact):
            label = scan_injection(text or "")
            if label:
                _fail(checks, "PROMPT_INJECTION", f"{path}:{label}")
                return

    def _check_budgets(self, artifact, checks):
        from backend.app.creator.validation import BUDGET_BOUNDS

        budgets = artifact["definition"].get("budgets", {})
        for field, (low, high) in BUDGET_BOUNDS.items():
            value = budgets.get(field)
            if not isinstance(value, int) or not low <= value <= high:
                _fail(checks, "BUDGET_SANITY", f"budgets.{field}")
                return

    def _check_workflow(self, artifact, checks):
        from backend.agent.platform.workflow import validate_workflow

        try:
            validate_workflow(artifact["definition"].get("workflow"))
        except ValueError as exc:
            _fail(checks, "WORKFLOW_VALID", getattr(exc, "code", "WORKFLOW_INVALID"))

    def _check_permission_ceiling(self, submission, artifact, checks):
        """冻结工件绑定的工具集必须仍在家庭权限上限内（启用 + 目录一致 + 已授权）。"""
        from backend.agent.platform.catalog import BuiltinToolRegistry
        from backend.app.db.models import AgentToolBinding, ToolDefinition, ToolPermission

        registry = BuiltinToolRegistry.default()
        tools = self.session.scalars(select(ToolDefinition).join(
            AgentToolBinding, AgentToolBinding.tool_id == ToolDefinition.id).where(
            AgentToolBinding.agent_version_id == submission.agent_version_id,
            AgentToolBinding.household_id == submission.household_id).order_by(
            AgentToolBinding.id)).all()
        declared = artifact["definition"].get("tools") or []
        if declared and len(tools) != len(declared):
            _fail(checks, "PERMISSION_CEILING", "TOOL_BINDING_MISSING")
            return
        for tool in tools:
            try:
                metadata = registry.resolve(tool.key).metadata
            except KeyError:
                _fail(checks, "PERMISSION_CEILING", "TOOL_NOT_IN_CATALOG")
                continue
            if not tool.enabled:
                _fail(checks, "PERMISSION_CEILING", "TOOL_NOT_ENABLED")
                continue
            if metadata.version != tool.version or metadata.risk_level != tool.risk_level:
                _fail(checks, "PERMISSION_CEILING", "TOOL_CATALOG_MISMATCH")
                continue
            permission = self.session.scalar(select(ToolPermission).where(
                ToolPermission.household_id == submission.household_id,
                ToolPermission.tool_id == tool.id))
            if permission is None or permission.tool_version != tool.version:
                _fail(checks, "PERMISSION_CEILING", "TOOL_NOT_GRANTED")

    def _check_attachments(self, submission, artifact, checks):
        """冻结工件引用的知识资产必须仍为 active 且扫描通过。"""
        from backend.app.db.models import CreatorKnowledgeAsset

        for skill_definition in self._skill_definitions(artifact):
            for attachment in skill_definition.get("attachments", []):
                if attachment.get("kind") != "knowledge":
                    continue
                asset = self.session.get(CreatorKnowledgeAsset, attachment.get("asset_id"))
                if (asset is None or asset.household_id != submission.household_id
                        or asset.status != "active" or asset.scan_status != "passed"):
                    _fail(checks, "ATTACHMENT_SCAN", attachment.get("asset_id"))
                    return

    def _check_sandbox_trial(self, submission, checks):
        """最近一次试运行必须完成、全用例通过且对应提交时的冻结定义（陈旧即拒绝）。"""
        from backend.app.db.models import CreatorTrialReport

        trial = self.session.scalar(select(CreatorTrialReport).where(
            CreatorTrialReport.household_id == submission.household_id,
            CreatorTrialReport.draft_id == submission.draft_id).order_by(
            CreatorTrialReport.created_at.desc(), CreatorTrialReport.id.desc()))
        if trial is None:
            _fail(checks, "SANDBOX_TRIAL", "SANDBOX_TRIAL_REQUIRED")
            return
        if trial.status != "completed":
            _fail(checks, "SANDBOX_TRIAL", trial.error_code or "SANDBOX_TRIAL_NOT_COMPLETED")
            return
        cases = json.loads(trial.cases_json or "[]")
        failed = [case for case in cases if case.get("status") != "passed"]
        if not cases or failed:
            _fail(checks, "SANDBOX_TRIAL",
                  failed[0].get("error_code") if failed else "SANDBOX_TRIAL_NO_CASES")
            return
        if self._trial_stale(trial, submission):
            _fail(checks, "SANDBOX_TRIAL", "SANDBOX_TRIAL_STALE")

    def _trial_stale(self, trial, submission) -> bool:
        """试运行证据必须对应提交时固化的不可变定义；草稿编辑后必须重新试运行。"""
        from backend.app.db.models import AgentVersion

        if not trial.agent_version_id:
            return True
        trial_version = self.session.get(AgentVersion, trial.agent_version_id)
        submitted_version = self.session.get(AgentVersion, submission.agent_version_id)
        if trial_version is None or submitted_version is None:
            return True
        return trial_version.definition_json != submitted_version.definition_json
