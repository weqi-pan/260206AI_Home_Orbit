# -*- coding: utf-8 -*-
"""Phase 7 marketplace: discovery, pinned installs and explicit upgrades.

Discovery searches published entries by name/description with category filter
and merges the household's official agents as official content. Deprecated
entries are hidden and unsearchable. Installs copy the immutable reviewed
version into the household through the Phase 3 version/deployment/installation
services, pin the installation to that reviewed version and grant tools with
default-deny per-tool authorization (low-risk auto per existing policy,
reversible/high only on explicit opt-in). Upgrades are explicit: new
permissions required by the new version are never silently inherited.
Knowledge attachments of the reviewed version are re-vectorized into the
installer household (cross-household isolation preserved).
"""
import json

from sqlalchemy import select

from backend.app.db.base import utcnow


class MarketError(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


def version_tools(session, household_id, agent_version_id):
    """Risk-labeled tools bound to an (immutable) agent version."""
    from backend.app.db.models import AgentToolBinding, ToolDefinition

    rows = session.scalars(select(ToolDefinition).join(
        AgentToolBinding, AgentToolBinding.tool_id == ToolDefinition.id).where(
        AgentToolBinding.agent_version_id == agent_version_id,
        AgentToolBinding.household_id == household_id).order_by(AgentToolBinding.id)).all()
    return [{"tool_id": row.id, "key": row.key, "display_name": row.display_name,
             "description": row.description, "risk_level": row.risk_level, "version": row.version}
            for row in rows]


class MarketListingService:
    def __init__(self, session):
        self.session = session

    def _published_entries(self, query=None, category=None):
        from backend.app.db.models import MarketEntry

        statement = select(MarketEntry).where(MarketEntry.status == "published").order_by(
            MarketEntry.install_count.desc(), MarketEntry.created_at.desc())
        if category:
            statement = statement.where(MarketEntry.category == category)
        rows = self.session.scalars(statement).all()
        if query:
            rows = [row for row in rows if query in row.name or query in row.description]
        return rows

    def listings(self, household_id, query=None, category=None):
        install_service = MarketInstallService(self.session)
        entries = []
        for entry in self._published_entries(query=query, category=category):
            entries.append({
                "id": entry.id, "name": entry.name, "description": entry.description,
                "category": entry.category, "official": entry.official,
                "install_count": entry.install_count, "current_version": entry.current_version_number,
                "my_installation": install_service.installation_state(household_id, entry.id),
            })
        return {"official": self._official(household_id, query), "entries": entries}

    def _official(self, household_id, query=None):
        from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation, AgentVersion)

        rows = self.session.execute(select(AgentDeployment, AgentDefinition, AgentVersion, AgentInstallation).join(
            AgentVersion, AgentVersion.id == AgentDeployment.agent_version_id).join(
            AgentDefinition, AgentDefinition.id == AgentVersion.agent_id).join(
            AgentInstallation, AgentInstallation.deployment_id == AgentDeployment.id).where(
            AgentDeployment.household_id == household_id, AgentDeployment.visibility == "official",
            AgentInstallation.household_id == household_id, AgentInstallation.status == "installed",
            AgentInstallation.authority_slot == "active",
            AgentInstallation.agent_version_id == AgentDeployment.agent_version_id).order_by(
            AgentDefinition.name)).all()
        result = []
        for deployment, definition, version, _installation in rows:
            description = json.loads(version.definition_json or "{}").get("metadata", {}).get("description", "")
            if query and query not in definition.name and query not in description:
                continue
            result.append({"deployment_id": deployment.id, "name": definition.name, "description": description,
                           "official": True, "installed": True})
        return result

    def entry_or_404(self, entry_id):
        from backend.app.db.models import MarketEntry

        entry = self.session.get(MarketEntry, entry_id)
        if entry is None:
            raise MarketError("MARKET_ENTRY_NOT_FOUND", 404)
        return entry

    def detail(self, household_id, entry_id):
        from backend.app.db.models import AgentVersion, Favorite, MarketEntry, ReviewSubmission

        entry = self.session.get(MarketEntry, entry_id)
        if entry is None or entry.status != "published":
            # 下架条目从发现中隐藏且不可搜索；已安装家庭通过 /market/my 看到 deprecated 状态
            raise MarketError("MARKET_ENTRY_NOT_FOUND", 404)
        versions = self.session.execute(select(ReviewSubmission, AgentVersion).join(
            AgentVersion, AgentVersion.id == ReviewSubmission.agent_version_id).where(
            ReviewSubmission.entry_id == entry.id, ReviewSubmission.status == "approved").order_by(
            AgentVersion.version.desc())).all()
        favorited = self.session.scalar(select(Favorite).where(Favorite.household_id == household_id,
                                                               Favorite.entry_id == entry.id)) is not None
        return {
            "id": entry.id, "name": entry.name, "description": entry.description,
            "category": entry.category, "official": entry.official, "status": entry.status,
            "install_count": entry.install_count,
            "current_version": {"number": entry.current_version_number,
                                "agent_version_id": entry.current_version_id},
            "versions": [{"number": version.version, "submission_id": submission.id,
                          "published_at": submission.decided_at} for submission, version in versions],
            "permissions": version_tools(self.session, entry.source_household_id, entry.current_version_id),
            "favorited": favorited,
            "my_installation": MarketInstallService(self.session).installation_state(household_id, entry.id),
        }

    # ------------------------------------------------------------- favorites
    def favorite(self, household_id, entry_id):
        from backend.app.db.models import Favorite

        entry = self.entry_or_404(entry_id)
        if self.session.scalar(select(Favorite).where(Favorite.household_id == household_id,
                                                      Favorite.entry_id == entry.id)) is None:
            self.session.add(Favorite(household_id=household_id, entry_id=entry.id))
            self.session.commit()
        return {"entry_id": entry.id, "favorited": True}

    def unfavorite(self, household_id, entry_id):
        from backend.app.db.models import Favorite

        row = self.session.scalar(select(Favorite).where(Favorite.household_id == household_id,
                                                         Favorite.entry_id == entry_id))
        if row is not None:
            self.session.delete(row)
            self.session.commit()
        return {"entry_id": entry_id, "favorited": False}

    def favorites(self, household_id):
        from backend.app.db.models import Favorite, MarketEntry

        rows = self.session.scalars(select(MarketEntry).join(
            Favorite, Favorite.entry_id == MarketEntry.id).where(
            Favorite.household_id == household_id).order_by(Favorite.created_at.desc())).all()
        return [{"id": row.id, "name": row.name, "description": row.description, "category": row.category,
                 "official": row.official, "install_count": row.install_count,
                 "current_version": row.current_version_number} for row in rows]

    # ---------------------------------------------------------------- reports
    def report(self, household_id, entry_id, account_id, reason):
        from backend.app.db.models import Report

        entry = self.entry_or_404(entry_id)
        if not (reason or "").strip():
            raise MarketError("REPORT_REASON_REQUIRED", 422)
        row = Report(household_id=household_id, entry_id=entry.id, reporter_account_id=account_id,
                     reason=reason[:500], status="open")
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row


class MarketInstallService:
    def __init__(self, session, executor=None):
        self.session = session
        self._executor = executor

    # ------------------------------------------------------------------ state
    def active_installation(self, household_id, entry_id):
        """Returns (market_install, installation) for the active pinned install."""
        from backend.app.db.models import AgentInstallation, MarketInstall

        rows = self.session.execute(select(MarketInstall, AgentInstallation).join(
            AgentInstallation, AgentInstallation.id == MarketInstall.installation_id).where(
            MarketInstall.household_id == household_id, MarketInstall.entry_id == entry_id,
            AgentInstallation.status == "installed", AgentInstallation.authority_slot == "active")).all()
        return rows[0] if rows else None

    def installation_state(self, household_id, entry_id):
        found = self.active_installation(household_id, entry_id)
        if found is None:
            return None
        market_install, installation = found
        return {"installation_id": installation.id, "deployment_id": installation.deployment_id,
                "pinned_version": market_install.pinned_version_number}

    def my_installs(self, household_id):
        from backend.app.db.models import AgentInstallation, MarketEntry, MarketInstall

        rows = self.session.execute(select(MarketInstall, AgentInstallation, MarketEntry).join(
            AgentInstallation, AgentInstallation.id == MarketInstall.installation_id).join(
            MarketEntry, MarketEntry.id == MarketInstall.entry_id).where(
            MarketInstall.household_id == household_id).order_by(MarketInstall.created_at.desc())).all()
        result = []
        seen_entries = set()
        for market_install, installation, entry in rows:
            if entry.id in seen_entries or installation.status != "installed" \
                    or installation.authority_slot != "active":
                continue
            seen_entries.add(entry.id)
            item = {
                "installation_id": installation.id,
                "entry": {"id": entry.id, "name": entry.name, "description": entry.description,
                          "category": entry.category},
                "deployment_id": installation.deployment_id,
                "agent_version_id": installation.agent_version_id,
                "pinned_version": market_install.pinned_version_number,
                "latest_version": entry.current_version_number,
                "update_available": (entry.status == "published"
                                     and entry.current_version_number > market_install.pinned_version_number),
                "deprecated": entry.status != "published",
                "granted_tool_keys": self._granted_keys(installation.id),
            }
            if item["update_available"]:
                item["update"] = self._update_plan(household_id, entry, market_install)
            result.append(item)
        return result

    def _update_plan(self, household_id, entry, market_install):
        new_tools = version_tools(self.session, entry.source_household_id, entry.current_version_id)
        old_keys = {item["key"] for item in version_tools(
            self.session, household_id, market_install.agent_version_id)}
        return {"from_version": market_install.pinned_version_number,
                "to_version": entry.current_version_number,
                "new_permissions": [item for item in new_tools if item["key"] not in old_keys]}

    def _granted_keys(self, installation_id):
        from backend.app.db.models import InstallationPermission, ToolDefinition

        rows = self.session.execute(select(ToolDefinition.key).join(
            InstallationPermission, InstallationPermission.tool_id == ToolDefinition.id).where(
            InstallationPermission.installation_id == installation_id)).all()
        return [row[0] for row in rows]

    # ----------------------------------------------------------------- install
    def install(self, household_id, account_id, entry_id, granted_tool_keys, role="owner"):
        from backend.app.db.models import (AgentDeployment, MarketEntry, MarketInstall)

        if role not in {"owner", "admin"}:
            raise MarketError("INSTALLATION_PERMISSION_FORBIDDEN", 403)
        entry = self.session.get(MarketEntry, entry_id)
        if entry is None:
            raise MarketError("MARKET_ENTRY_NOT_FOUND", 404)
        if entry.status != "published":
            raise MarketError("MARKET_ENTRY_DEPRECATED")
        if self.active_installation(household_id, entry.id) is not None:
            raise MarketError("ALREADY_INSTALLED")
        source_version = self.session.get(self._version_model(), entry.current_version_id)
        if source_version is None:
            raise MarketError("MARKET_ENTRY_VERSION_MISSING", 409)
        required_tools = version_tools(self.session, entry.source_household_id, entry.current_version_id)
        self._validate_requested(required_tools, granted_tool_keys)

        prior = self.session.scalar(select(MarketInstall).where(
            MarketInstall.household_id == household_id, MarketInstall.entry_id == entry.id).order_by(
            MarketInstall.created_at.desc()))
        deployment = None
        if prior is not None:
            deployment = self.session.get(AgentDeployment, prior.deployment_id)
            if deployment is not None and deployment.household_id != household_id:
                deployment = None

        agent, local_version = self._copy_version(household_id, entry, source_version,
                                                  reuse_agent_version_id=prior.agent_version_id if prior else None)
        if deployment is None:
            deployment = AgentDeployment(household_id=household_id, agent_version_id=local_version.id,
                                         visibility="market")
            self.session.add(deployment)
            self.session.flush()
        elif deployment.agent_version_id != local_version.id:
            from backend.agent.platform.versions import AgentVersionService

            # 重装/复用既有本地部署：指针先移到本次复制的固定版本
            AgentVersionService(self.session, household_id).switch(deployment.id, local_version.id)

        from backend.agent.platform.installations import InstallationService

        installation = InstallationService(self.session, household_id, account_id).install(deployment.id)
        granted = self._grant_tools(household_id, installation.id, account_id, required_tools,
                                    granted_tool_keys)
        first_install = self.session.scalar(select(MarketInstall.id).where(
            MarketInstall.household_id == household_id, MarketInstall.entry_id == entry.id)) is None
        if first_install:
            entry.install_count += 1
        self.session.add(MarketInstall(household_id=household_id, entry_id=entry.id,
                                       installation_id=installation.id, deployment_id=deployment.id,
                                       agent_version_id=local_version.id,
                                       pinned_version_number=entry.current_version_number,
                                       installed_by_account_id=account_id))
        self.session.commit()
        self._revectorize_knowledge(household_id, local_version.id)
        return {"installation_id": installation.id, "deployment_id": deployment.id,
                "agent_version_id": local_version.id, "pinned_version": entry.current_version_number,
                "granted_tool_keys": granted, "required_tools": required_tools}

    def _validate_requested(self, tool_infos, granted_tool_keys):
        unknown = set(granted_tool_keys or []) - {item["key"] for item in tool_infos}
        if unknown:
            raise MarketError("MARKET_TOOL_NOT_IN_ENTRY", 422)

    def _copy_version(self, household_id, entry, source_version, reuse_agent_version_id=None):
        """Copies the immutable reviewed version into the installer household.

        The reviewed version is never mutated; the local copy revalidates the
        frozen definition through the strict schema services. Returns
        (agent, local_version_view).
        """
        from backend.agent.platform.versions import AgentVersionService, SkillVersionService
        from backend.app.db.models import (AgentDefinition, AgentSkillBinding, AgentToolBinding, AgentVersion,
                                           Skill, SkillVersion)

        agent = None
        if reuse_agent_version_id:
            reused = self.session.get(AgentVersion, reuse_agent_version_id)
            if reused is not None and reused.household_id == household_id:
                agent = self.session.get(AgentDefinition, reused.agent_id)
        if agent is None:
            agent = AgentDefinition(household_id=household_id, name=entry.name[:100])
            self.session.add(agent)
            self.session.flush()

        local_skill_versions = []
        bindings = self.session.execute(select(AgentSkillBinding, SkillVersion).join(
            SkillVersion, SkillVersion.id == AgentSkillBinding.skill_version_id).where(
            AgentSkillBinding.agent_version_id == source_version.id,
            AgentSkillBinding.household_id == source_version.household_id).order_by(
            AgentSkillBinding.id)).all()
        for _binding, source_skill in bindings:
            # 以来源技能行 id 作为本家庭技能名，保证跨版本升级复用同一本地技能
            local_skill = self.session.scalar(select(Skill).where(
                Skill.household_id == household_id, Skill.name == source_skill.id))
            if local_skill is None:
                local_skill = Skill(household_id=household_id, name=source_skill.id)
                self.session.add(local_skill)
                self.session.flush()
            local_skill_versions.append(SkillVersionService(self.session, household_id).publish(
                local_skill.id, json.loads(source_skill.definition_json)))

        local_version = AgentVersionService(self.session, household_id).publish(
            agent.id, json.loads(source_version.definition_json))
        for skill_version in local_skill_versions:
            self.session.add(AgentSkillBinding(household_id=household_id,
                                               agent_version_id=local_version.id,
                                               skill_version_id=skill_version.id))
        for binding in self.session.scalars(select(AgentToolBinding).where(
                AgentToolBinding.agent_version_id == source_version.id,
                AgentToolBinding.household_id == source_version.household_id).order_by(
                AgentToolBinding.id)).all():
            self.session.add(AgentToolBinding(household_id=household_id,
                                              agent_version_id=local_version.id,
                                              tool_id=binding.tool_id))
        self.session.commit()
        return agent, local_version

    def _grant_tools(self, household_id, installation_id, account_id, tool_infos, granted_tool_keys):
        """Default-deny per-tool authorization mirroring existing policy:
        low-risk auto-granted, reversible/high only on explicit opt-in."""
        from backend.app.db.models import InstallationPermission

        requested = set(granted_tool_keys or [])
        granted = []
        for item in tool_infos:
            if item["risk_level"] != "low" and item["key"] not in requested:
                continue
            self.session.add(InstallationPermission(
                household_id=household_id, installation_id=installation_id,
                tool_id=item["tool_id"], tool_version=item["version"],
                granted_by_account_id=account_id))
            granted.append(item["key"])
        self.session.commit()
        return granted

    def _revectorize_knowledge(self, household_id, local_agent_version_id):
        """Copies reviewed knowledge attachments into the installer household
        through the knowledge.vectorize task path (idempotent by record key)."""
        from backend.app.db.models import AgentSkillBinding, SkillVersion

        rows = self.session.scalars(select(SkillVersion).join(
            AgentSkillBinding, AgentSkillBinding.skill_version_id == SkillVersion.id).where(
            AgentSkillBinding.agent_version_id == local_agent_version_id,
            AgentSkillBinding.household_id == household_id).order_by(AgentSkillBinding.id)).all()
        jobs = []
        for local_skill_version in rows:
            for attachment in json.loads(local_skill_version.definition_json or "{}").get("attachments", []):
                if attachment.get("kind") == "knowledge":
                    jobs.append((attachment.get("asset_id"), local_skill_version.id))
        if not jobs:
            return []
        executor = self._executor_service()
        results = []
        for asset_id, skill_version_id in jobs:
            key = f"vectorize:market:{asset_id}:{skill_version_id}:{household_id}"
            executor.submit("knowledge.vectorize",
                            {"creator_asset_id": asset_id, "skill_version_id": skill_version_id,
                             "target_household_id": household_id},
                            key, household_id=household_id)
            record = executor.run(key)
            results.append({"asset_id": asset_id, "skill_version_id": skill_version_id,
                            "status": record.status, "error_code": record.error_code})
        return results

    def _executor_service(self):
        if self._executor is None:
            from sqlalchemy.orm import sessionmaker

            from backend.app.tasks.executor import TaskExecutor

            self._executor = TaskExecutor(sessionmaker(bind=self.session.get_bind(), expire_on_commit=False))
        return self._executor

    # ----------------------------------------------------------------- upgrade
    def upgrade(self, household_id, account_id, installation_id, granted_tool_keys, role="owner"):
        from backend.agent.platform.installations import InstallationService
        from backend.app.db.models import (AgentInstallation, InstallationPermission, MarketEntry, MarketInstall)

        if role not in {"owner", "admin"}:
            raise MarketError("INSTALLATION_PERMISSION_FORBIDDEN", 403)
        market_install = self.session.scalar(select(MarketInstall).where(
            MarketInstall.installation_id == installation_id, MarketInstall.household_id == household_id))
        current = self.session.get(AgentInstallation, installation_id)
        if (market_install is None or current is None or current.household_id != household_id
                or current.status != "installed" or current.authority_slot != "active"):
            raise MarketError("INSTALLATION_NOT_FOUND", 404)
        entry = self.session.get(MarketEntry, market_install.entry_id)
        if entry is None:
            raise MarketError("MARKET_ENTRY_NOT_FOUND", 404)
        if entry.status != "published":
            raise MarketError("MARKET_ENTRY_DEPRECATED")
        if entry.current_version_number <= market_install.pinned_version_number:
            raise MarketError("UPDATE_NOT_AVAILABLE")
        source_version = self.session.get(self._version_model(), entry.current_version_id)
        new_tools = version_tools(self.session, entry.source_household_id, entry.current_version_id)
        old_keys = {item["key"] for item in version_tools(self.session, household_id,
                                                          market_install.agent_version_id)}
        self._validate_requested(new_tools, granted_tool_keys)
        old_permissions = self.session.scalars(select(InstallationPermission).where(
            InstallationPermission.installation_id == installation_id)).all()
        old_tool_ids = {row.tool_id for row in old_permissions}

        agent, local_version = self._copy_version(
            household_id, entry, source_version,
            reuse_agent_version_id=market_install.agent_version_id)
        installation = InstallationService(self.session, household_id, account_id).upgrade(
            market_install.deployment_id, local_version.id)

        # 旧授权延续到新安装；新版本新增（或此前被拒）的工具必须显式重新授权，绝不静默继承
        requested = set(granted_tool_keys or [])
        carried, granted = [], []
        for item in new_tools:
            if item["tool_id"] in old_tool_ids:
                self.session.add(InstallationPermission(
                    household_id=household_id, installation_id=installation.id,
                    tool_id=item["tool_id"], tool_version=item["version"],
                    granted_by_account_id=account_id))
                carried.append(item["key"])
            elif item["key"] in requested:
                self.session.add(InstallationPermission(
                    household_id=household_id, installation_id=installation.id,
                    tool_id=item["tool_id"], tool_version=item["version"],
                    granted_by_account_id=account_id))
                granted.append(item["key"])
        self.session.add(MarketInstall(household_id=household_id, entry_id=entry.id,
                                       installation_id=installation.id,
                                       deployment_id=market_install.deployment_id,
                                       agent_version_id=local_version.id,
                                       pinned_version_number=entry.current_version_number,
                                       installed_by_account_id=account_id, upgraded_at=utcnow()))
        self.session.commit()
        self._revectorize_knowledge(household_id, local_version.id)
        return {"installation_id": installation.id, "deployment_id": market_install.deployment_id,
                "agent_version_id": local_version.id, "pinned_version": entry.current_version_number,
                "granted_tool_keys": carried + granted, "newly_granted_tool_keys": granted,
                "update": {"from_version": market_install.pinned_version_number,
                           "to_version": entry.current_version_number,
                           "new_tool_keys": [item["key"] for item in new_tools
                                             if item["key"] not in old_keys]}}

    # --------------------------------------------------------------- uninstall
    def uninstall(self, household_id, installation_id, revoke_permissions=False, role="owner"):
        from sqlalchemy import delete

        if role not in {"owner", "admin"}:
            raise MarketError("INSTALLATION_PERMISSION_FORBIDDEN", 403)
        from backend.app.db.models import AgentInstallation, InstallationPermission, MarketInstall

        market_install = self.session.scalar(select(MarketInstall).where(
            MarketInstall.installation_id == installation_id, MarketInstall.household_id == household_id))
        installation = self.session.get(AgentInstallation, installation_id)
        if market_install is None or installation is None or installation.household_id != household_id:
            raise MarketError("INSTALLATION_NOT_FOUND", 404)
        installation.status = "uninstalled"
        installation.authority_slot = None
        if revoke_permissions:
            self.session.execute(delete(InstallationPermission).where(
                InstallationPermission.installation_id == installation_id,
                InstallationPermission.household_id == household_id))
        self.session.commit()
        return installation

    def _version_model(self):
        from backend.app.db.models import AgentVersion

        return AgentVersion
