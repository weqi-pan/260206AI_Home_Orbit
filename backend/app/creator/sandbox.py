# -*- coding: utf-8 -*-
"""Phase 6 sandbox trial: executes a validated creator draft through the
secured AgentCompiler/runner with a dedicated sandbox installation.

Trust rules:
- The draft is re-validated before execution; nothing unvalidated runs.
- Tools are limited to deterministic/low-risk tools plus explicitly granted
  reversible tools; high-risk tools are never granted in the sandbox.
- The sandbox never grants household-level permissions (ToolPermission) or
  permissions on any other installation, and every tool call goes through the
  platform executor so ToolExecution audit and PERMISSION_REQUIRED semantics
  are enforced.
- Trial stacks are pinned on the report row so retry after a crash reuses the
  immutable versions instead of duplicating them.
"""
import json

from sqlalchemy import select

from backend.app.db.base import utcnow


class SandboxTrialError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _stable_code(exc) -> str:
    from backend.agent.platform.runner import stable_run_code

    return stable_run_code(exc)


class SandboxTrialService:
    def __init__(self, session_or_factory, household_id, principal_id, settings=None, model=None,
                 model_resolver=None, vector_service=None, registry=None):
        from sqlalchemy.orm import Session

        if isinstance(session_or_factory, Session):
            self._own_factory = None
            self.session = session_or_factory
        else:
            self._own_factory = session_or_factory
            self.session = session_or_factory()
        self.session_factory = self._own_factory or _factory_for(self.session)
        self.household_id = household_id
        self.principal_id = principal_id
        self.settings = settings
        self.model = model
        self.model_resolver = model_resolver
        self.vector_service = vector_service
        self.registry = registry

    # ------------------------------------------------------------------ report
    def create_report(self, draft_id, granted_tool_keys):
        from backend.app.db.models import CreatorDraft, CreatorTrialReport

        draft = self.session.get(CreatorDraft, draft_id)
        if draft is None or draft.household_id != self.household_id:
            raise SandboxTrialError("DRAFT_NOT_FOUND")
        report = CreatorTrialReport(household_id=self.household_id, draft_id=draft_id, status="pending",
                                    granted_tool_keys_json=json.dumps(list(granted_tool_keys), ensure_ascii=False))
        self.session.add(report)
        self.session.commit()
        return report

    def _summary(self, report):
        cases = json.loads(report.cases_json or "[]")
        return {"id": report.id, "draft_id": report.draft_id, "status": report.status,
                "cases": cases, "error_code": report.error_code,
                "granted_tool_keys": json.loads(report.granted_tool_keys_json or "[]")}

    def run(self, report_id) -> dict:
        from backend.app.db.models import CreatorTrialReport

        try:
            report = self.session.get(CreatorTrialReport, report_id)
            if report is None or report.household_id != self.household_id:
                raise SandboxTrialError("TRIAL_REPORT_NOT_FOUND")
            if report.status == "completed":
                return self._summary(report)
            report.status = "running"
            self.session.commit()
            try:
                return self._execute(report)
            except SandboxTrialError as exc:
                report.status = "failed"
                report.error_code = exc.code
                report.finished_at = utcnow()
                self.session.commit()
                return self._summary(report)
        finally:
            if self._own_factory is not None:
                self.session.close()

    # ----------------------------------------------------------------- execute
    def _fail_cases(self, report, code):
        card_tests = self._card_tests(report)
        cases = [{"name": case["name"], "input": case["input"], "status": "failed",
                  "error_code": code, "output": ""} for case in card_tests]
        report.cases_json = json.dumps(cases, ensure_ascii=False)
        report.status = "completed"
        report.error_code = None
        report.finished_at = utcnow()
        self.session.commit()
        return self._summary(report)

    def _card_tests(self, report):
        card = self._load_card(report)
        return card.get("tests", []) if card else []

    def _load_card(self, report):
        from backend.app.db.models import CreatorDraft

        draft = self.session.get(CreatorDraft, report.draft_id)
        if draft is None or not draft.intent_card_json:
            raise SandboxTrialError("SANDBOX_DRAFT_EMPTY")
        return json.loads(draft.intent_card_json)

    def _execute(self, report) -> dict:
        from backend.app.creator.validation import DraftValidator, DraftValidationRejected

        card = self._load_card(report)
        validator = DraftValidator(self.session, self.household_id, registry=self.registry)
        try:
            validated = validator.validate(card, draft_id=report.draft_id)
        except DraftValidationRejected as exc:
            raise SandboxTrialError(f"SANDBOX_{exc.errors[0]['code']}") from exc

        stack = self._ensure_stack(report, card, validated)
        model = self._resolve_model() if not card.get("workflow") else None
        if not card.get("workflow") and model is None:
            return self._fail_cases(report, self._model_error_code)
        self._grant_sandbox_permissions(report, validated)
        self._last_context = self._build_context(report, card, stack)
        from backend.agent.platform.compiler import AgentCompiler

        compile_kwargs = {"model_factory": (lambda _deployment: model)} if model is not None else {}
        compiler = AgentCompiler(self.session, **compile_kwargs)
        compiled = compiler.compile(stack["deployment"].id, self._last_context)

        cases = []
        for case in card.get("tests", []):
            cases.append(self._run_metered_case(compiled, case, report, card))
        report.cases_json = json.dumps(cases, ensure_ascii=False)
        report.status = "completed"
        report.error_code = None
        report.finished_at = utcnow()
        self._teardown(stack)
        from backend.agent.platform.runner import persist_run_outcome

        persist_run_outcome(self.session, stack["run"].id, "completed", finished=True)
        self.session.commit()
        return self._summary(report)

    _model_error_code = "MODEL_NOT_CONFIGURED"
    _quota_model_id: str | None = None
    _quota_budget: int = 0

    def _resolve_model(self):
        if self.model is not None:
            return self.model
        from backend.app.llm_runtime.quotas import QuotaExceeded
        from backend.app.llm_runtime.runtime_service import ModelRuntimeError, ModelRuntimeService

        try:
            if self.model_resolver is not None:
                self.model_resolver("primary_chat")
            service = ModelRuntimeService(self.session)
            resolved = service.resolve("primary_chat", household_id=self.household_id)
            model = service.build_chat_model(resolved)
            profile = resolved.snapshot.profile
            budget = profile["agent_token_budget"] or profile["max_tokens"]
            if not budget or budget <= 0:
                self._model_error_code = "MODEL_REQUEST_BUDGET_NOT_CONFIGURED"
                return None
            self._quota_model_id = resolved.snapshot.model_id
            self._quota_budget = int(budget)
            return model
        except (ModelRuntimeError, QuotaExceeded) as exc:
            self._model_error_code = getattr(exc, "code", _stable_code(exc))
            return None
        except Exception as exc:
            self._model_error_code = _stable_code(exc)
            return None

    def _run_metered_case(self, compiled, case, report, card):
        """Platform-model sandbox cases go through the chat quota lifecycle:
        reserve before the request, complete after; over-limit is a silent
        stable failure (incident already persisted by QuotaService)."""
        from backend.app.llm_runtime.quotas import QuotaExceeded, QuotaService

        if self._quota_model_id is None:
            return self._run_case(compiled, case, report, card)
        try:
            usage = QuotaService(self.session).reserve(self.household_id, self._quota_model_id, self._quota_budget)
        except QuotaExceeded as exc:
            return _failed_case(case, exc.code, "")
        try:
            result = self._run_case(compiled, case, report, card)
        except Exception:
            QuotaService(self.session).complete(usage.id, status="failed")
            raise
        QuotaService(self.session).complete(usage.id)
        return result

    # -------------------------------------------------------------- stack build
    def _ensure_stack(self, report, card, validated):
        from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation, AgentRun,
                                           AgentSkillBinding, AgentToolBinding, AgentVersion, CreatorDraft,
                                           Skill)

        if report.agent_version_id:
            agent_version = self.session.get(AgentVersion, report.agent_version_id)
            deployment = self.session.get(AgentDeployment, report.deployment_id)
            installation = self.session.get(AgentInstallation, report.installation_id)
            run = self.session.get(AgentRun, report.run_id)
            if agent_version and deployment and installation and run:
                if installation.authority_slot != "active" or installation.status != "installed":
                    installation.status = "installed"
                    installation.authority_slot = "active"
                    self.session.commit()
                return {"agent_version": agent_version, "deployment": deployment,
                        "installation": installation, "run": run}

        linked_agent_id = self.session.scalar(select(CreatorDraft.agent_definition_id).where(
            CreatorDraft.id == report.draft_id))
        agent = self.session.get(AgentDefinition, linked_agent_id) if linked_agent_id else None
        if agent is None:
            agent = AgentDefinition(household_id=self.household_id, name=card["name"][:100])
            self.session.add(agent)
            self.session.flush()
            draft = self.session.get(CreatorDraft, report.draft_id)
            draft.agent_definition_id = agent.id

        skill_versions = []
        for skill in validated["skills"]:
            skill_name = f"{card['name']}-{skill['name']}"[:100]
            row = self.session.scalar(select(Skill).where(Skill.household_id == self.household_id,
                                                          Skill.name == skill_name))
            if row is None:
                row = Skill(household_id=self.household_id, name=skill_name)
                self.session.add(row)
                self.session.flush()
            from backend.agent.platform.versions import SkillVersionService

            skill_version = SkillVersionService(self.session, self.household_id).publish(row.id, skill["definition"])
            skill_versions.append(skill_version)

        from backend.agent.platform.versions import AgentVersionService

        agent_version = AgentVersionService(self.session, self.household_id).publish(agent.id,
                                                                                     validated["agent_definition"])
        for skill_version in skill_versions:
            self.session.add(AgentSkillBinding(household_id=self.household_id,
                                               agent_version_id=agent_version.id,
                                               skill_version_id=skill_version.id))
        for info in validated["tools"].values():
            self.session.add(AgentToolBinding(household_id=self.household_id,
                                              agent_version_id=agent_version.id, tool_id=info["tool_id"]))
        deployment = AgentDeployment(household_id=self.household_id, agent_version_id=agent_version.id,
                                     visibility="sandbox")
        self.session.add(deployment)
        self.session.flush()
        installation = AgentInstallation(household_id=self.household_id, deployment_id=deployment.id,
                                         agent_version_id=agent_version.id, status="installed",
                                         authority_slot="active")
        self.session.add(installation)
        self.session.flush()
        member_id = self.session.scalar(select(self._member_model().id).where(
            self._member_model().account_id == self.principal_id,
            self._member_model().household_id == self.household_id))
        run = AgentRun(household_id=self.household_id, deployment_id=deployment.id,
                       principal_id=self.principal_id, member_id=member_id,
                       session_id=f"sandbox:{report.draft_id}:{report.id}",
                       agent_version_id=agent_version.id, deployment_revision=deployment.revision,
                       model_id="sandbox", model_revision=1, status="running", started_at=utcnow())
        self.session.add(run)
        self.session.flush()
        report.agent_version_id = agent_version.id
        report.deployment_id = deployment.id
        report.installation_id = installation.id
        report.run_id = run.id
        self.session.commit()
        return {"agent_version": agent_version, "deployment": deployment,
                "installation": installation, "run": run}

    def _grant_sandbox_permissions(self, report, validated):
        from backend.app.db.models import InstallationPermission

        requested = set(json.loads(report.granted_tool_keys_json or "[]"))
        for key, info in validated["tools"].items():
            if info["risk_level"] == "high":
                continue  # 沙盒永不授予高风险工具
            if info["risk_level"] != "low" and key not in requested:
                continue  # 可逆写工具必须显式确认
            existing = self.session.scalar(select(InstallationPermission).where(
                InstallationPermission.installation_id == report.installation_id,
                InstallationPermission.tool_id == info["tool_id"]))
            if existing is None:
                self.session.add(InstallationPermission(
                    household_id=self.household_id, installation_id=report.installation_id,
                    tool_id=info["tool_id"], tool_version=info["version"],
                    granted_by_account_id=self.principal_id))
        self.session.commit()

    def _build_context(self, report, card, stack):
        from backend.agent.platform.context import RunBudget, RunContext
        from backend.agent.tools.household_gateways import HouseholdToolRepositories

        member = self.session.scalar(select(self._member_model()).where(
            self._member_model().account_id == self.principal_id,
            self._member_model().household_id == self.household_id))
        if member is None:
            raise SandboxTrialError("HOUSEHOLD_MEMBERSHIP_MISSING")
        budgets = card.get("budgets", {})
        from backend.agent.platform.authority import ExecutionAuthority

        authorities = ExecutionAuthority(self.session, registry=self.registry).tools(
            stack["deployment"].id, self.household_id, stack["agent_version"].id)
        grants = {item.key: {"version": item.version, "risk_level": item.risk_level} for item in authorities}
        return RunContext(
            principal_id=self.principal_id, household_id=self.household_id, member_id=member.id,
            role=member.role, session_id=f"sandbox:{report.draft_id}:{report.id}",
            deployment_id=stack["deployment"].id, agent_version_id=stack["agent_version"].id,
            run_id=stack["run"].id, timezone="Asia/Shanghai", grants=grants, consent={},
            deployment_revision=stack["deployment"].revision, model_id="sandbox", model_revision=1,
            model_snapshot={"id": "sandbox", "revision": 1},
            repositories=HouseholdToolRepositories(self.session_factory),
            vector_store_service=self.vector_service,
            budget=RunBudget(max_delegation_depth=int(budgets.get("delegation_depth", 4)),
                             max_tool_calls=int(budgets.get("tool_calls", 16)),
                             max_tokens=int(budgets.get("tokens", 32000)),
                             wall_time_seconds=int(budgets.get("wall_time_seconds", 120))))

    def _run_case(self, compiled, case, report, card):
        from backend.agent.platform.context import RunBudget, RunContext
        from backend.agent.platform.policy_errors import ApprovalRequired, PlatformToolError

        budgets = card.get("budgets", {})
        context = RunContext(**{**self._last_context.__dict__,
                                "budget": RunBudget(
                                    max_delegation_depth=int(budgets.get("delegation_depth", 4)),
                                    max_tool_calls=int(budgets.get("tool_calls", 16)),
                                    max_tokens=int(budgets.get("tokens", 32000)),
                                    wall_time_seconds=int(budgets.get("wall_time_seconds", 120)))})
        output = []
        try:
            for event in compiled.stream([{"role": "user", "content": case["input"]}], context):
                if event.get("event") == "token":
                    output.append(event.get("data", {}).get("content", ""))
            text = _sanitize("".join(output))
            passed = case["expected_contains"] in text
            return {"name": case["name"], "input": case["input"][:500],
                    "status": "passed" if passed else "failed",
                    "error_code": None if passed else "EXPECTATION_NOT_MET", "output": text}
        except (PlatformToolError, ApprovalRequired) as exc:
            code = getattr(exc, "code", "APPROVAL_REQUIRED")
            return _failed_case(case, code, _sanitize("".join(output)))
        except Exception as exc:
            return _failed_case(case, _stable_code(exc), _sanitize("".join(output)))

    _last_context = None

    def _teardown(self, stack):
        stack["installation"].status = "uninstalled"
        stack["installation"].authority_slot = None
        self.session.commit()

    def _member_model(self):
        from backend.app.db.models import HouseholdMember

        return HouseholdMember


def _failed_case(case, code, output):
    return {"name": case["name"], "input": case["input"][:500], "status": "failed",
            "error_code": code, "output": output}


def _sanitize(text: str) -> str:
    return " ".join(str(text).split())[:1000]


def _factory_for(session):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)
