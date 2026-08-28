# -*- coding: utf-8 -*-
"""Phase 6 private publishing.

Validated draft -> immutable Agent/Skill versions + private deployment +
household installation with explicit per-tool permission grants. Reversible
writes (and high-risk tools) are opt-in via the confirmed granted key list,
mirroring the official seeding policy. Knowledge assets are vectorized through
the Phase 5 ``knowledge.vectorize`` task path with household + skill-version
metadata so retrieval stays isolated. Republishing an edited draft creates a
new immutable version and moves the deployment pointer.
"""
import json

from sqlalchemy import select

from backend.app.db.base import utcnow


class PublishRejected(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


class PrivatePublishService:
    def __init__(self, session_or_factory, household_id, principal_id, settings=None, executor=None):
        from sqlalchemy.orm import Session

        if isinstance(session_or_factory, Session):
            self._owns_session = False
            self.session = session_or_factory
            from sqlalchemy.orm import sessionmaker

            self.session_factory = sessionmaker(bind=session_or_factory.get_bind(), expire_on_commit=False)
        else:
            self._owns_session = True
            self.session_factory = session_or_factory
            self.session = session_or_factory()
        self.household_id = household_id
        self.principal_id = principal_id
        self.settings = settings
        self._executor = executor

    # ----------------------------------------------------------------- helpers
    def _draft(self, draft_id):
        from backend.app.db.models import CreatorDraft

        draft = self.session.get(CreatorDraft, draft_id)
        if draft is None or draft.household_id != self.household_id:
            raise PublishRejected("DRAFT_NOT_FOUND")
        return draft

    def _executor_service(self):
        if self._executor is None:
            from backend.app.tasks.executor import TaskExecutor

            self._executor = TaskExecutor(self.session_factory)
        return self._executor

    # ------------------------------------------------------------------ publish
    def publish(self, draft_id, granted_tool_keys):
        from backend.app.creator.validation import DraftValidator, DraftValidationRejected
        from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation,
                                           AgentSkillBinding, AgentToolBinding, CreatorDraft, InstallationPermission,
                                           Skill)

        draft = self._draft(draft_id)
        if not draft.intent_card_json:
            raise PublishRejected("DRAFT_EMPTY")
        card = json.loads(draft.intent_card_json)
        validator = DraftValidator(self.session, self.household_id)
        try:
            validated = validator.validate(card, draft_id=draft_id)
        except DraftValidationRejected as exc:
            raise PublishRejected(exc.errors[0]["code"], detail=json.dumps(exc.errors, ensure_ascii=False)[:300]) from exc

        confirmed = self._confirmed_keys(set(granted_tool_keys), validator)

        agent = None
        if draft.agent_definition_id:
            agent = self.session.get(AgentDefinition, draft.agent_definition_id)
        if agent is None:
            agent = AgentDefinition(household_id=self.household_id, name=card["name"][:100])
            self.session.add(agent)
            self.session.flush()
            draft.agent_definition_id = agent.id

        skill_results = []
        skill_assets = []
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
            skill_results.append({"name": skill["name"], "skill_id": row.id, "skill_version_id": skill_version.id})
            for attachment in skill["definition"].get("attachments", []):
                skill_assets.append((skill_version.id, attachment["asset_id"]))

        from backend.agent.platform.versions import AgentVersionService

        agent_version = AgentVersionService(self.session, self.household_id).publish(agent.id,
                                                                                     validated["agent_definition"])
        for item in skill_results:
            self.session.add(AgentSkillBinding(household_id=self.household_id,
                                               agent_version_id=agent_version.id,
                                               skill_version_id=item["skill_version_id"]))
        tool_ids = []
        for info in validated["tools"].values():
            self.session.add(AgentToolBinding(household_id=self.household_id,
                                              agent_version_id=agent_version.id, tool_id=info["tool_id"]))
            tool_ids.append(info)
        self.session.commit()

        deployment = self._deployment_for(draft, agent_version)
        installation = self._installation_for(deployment, agent_version)
        self._grant_installation_permissions(validated, confirmed, installation.id)

        draft = self._draft(draft_id)
        draft.status = "private_published"
        draft.deployment_id = deployment.id
        draft.updated_at = utcnow()
        self.session.commit()

        vectorized = self._vectorize_assets(skill_assets)
        return {
            "agent_id": agent.id,
            "agent_version_id": agent_version.id,
            "agent_version": agent_version.version,
            "deployment": {"id": deployment.id, "revision": deployment.revision, "visibility": deployment.visibility},
            "installation_id": installation.id,
            "skills": skill_results,
            "granted_tool_keys": [info["key"] for info in tool_ids
                                  if info["risk_level"] == "low" or info["key"] in confirmed],
            "vectorized_assets": vectorized,
        }

    def _confirmed_keys(self, requested, validator):
        confirmed = set()
        for key in requested:
            info = validator._classify_tool(key)
            if info is None or "__code__" in info:
                raise PublishRejected(info["__code__"] if info else "TOOL_NOT_IN_CATALOG",
                                      detail=key)
            confirmed.add(key)
        return confirmed

    def _deployment_for(self, draft, agent_version):
        from backend.app.db.models import AgentDeployment
        from backend.agent.platform.versions import AgentVersionService

        deployment = None
        if draft.deployment_id:
            deployment = self.session.get(AgentDeployment, draft.deployment_id)
        if deployment is None:
            deployment = AgentDeployment(household_id=self.household_id,
                                         agent_version_id=agent_version.id, visibility="private")
            self.session.add(deployment)
            self.session.commit()
            return deployment
        return AgentVersionService(self.session, self.household_id).switch(deployment.id, agent_version.id)

    def _installation_for(self, deployment, agent_version):
        from backend.agent.platform.installations import InstallationService

        service = InstallationService(self.session, self.household_id, self.principal_id)
        existing = self.session.scalar(select(self._installation_model()).where(
            self._installation_model().deployment_id == deployment.id,
            self._installation_model().household_id == self.household_id,
            self._installation_model().authority_slot == "active"))
        if existing is not None and existing.agent_version_id == agent_version.id:
            return existing
        if existing is not None:
            return service.upgrade(deployment.id, agent_version.id)
        return service.install(deployment.id)

    def _grant_installation_permissions(self, validated, confirmed, installation_id):
        from backend.app.db.models import InstallationPermission

        for key, info in validated["tools"].items():
            if info["risk_level"] != "low" and key not in confirmed:
                continue
            existing = self.session.scalar(select(InstallationPermission).where(
                InstallationPermission.installation_id == installation_id,
                InstallationPermission.tool_id == info["tool_id"]))
            if existing is None:
                self.session.add(InstallationPermission(
                    household_id=self.household_id, installation_id=installation_id,
                    tool_id=info["tool_id"], tool_version=info["version"],
                    granted_by_account_id=self.principal_id))
        self.session.commit()

    def _vectorize_assets(self, skill_assets):
        """Vectorize creator assets through the knowledge.vectorize task path.

        SQLite preview/test mode runs the record synchronously (documented
        manual boundary); a failure leaves the record retry_scheduled for the
        worker instead of failing the publish itself.
        """
        results = []
        if not skill_assets:
            return results
        executor = self._executor_service()
        for skill_version_id, asset_id in skill_assets:
            key = f"vectorize:creator:{asset_id}:{skill_version_id}"
            record = executor.submit("knowledge.vectorize",
                                     {"creator_asset_id": asset_id, "skill_version_id": skill_version_id},
                                     key, household_id=self.household_id)
            record = executor.run(key)
            results.append({"asset_id": asset_id, "skill_version_id": skill_version_id,
                            "status": record.status, "error_code": record.error_code})
        return results

    def _installation_model(self):
        from backend.app.db.models import AgentInstallation

        return AgentInstallation
