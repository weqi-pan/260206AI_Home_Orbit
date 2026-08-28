"""Phase 5 official household agents (家庭厨师/家庭计划员/生活百科/家庭总管) seed tests."""
import json

from sqlalchemy import func, select


def _agents(db, household_id):
    from backend.app.db.models import AgentDefinition

    return {row.name: row for row in db.scalars(select(AgentDefinition).where(
        AgentDefinition.household_id == household_id)).all()}


def test_seed_creates_official_household_agents_with_versions_deployments_installations(client, app):
    from backend.app.db.seed import seed_database
    from backend.app.db.models import (Account, AgentDeployment, AgentInstallation, AgentVersion, HouseholdMember,
                                       InstallationPermission, ToolDefinition)

    seed_database(app.state.database.session_factory, app.state.settings)
    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        agents = _agents(db, household_id)
        assert {"设备管家", "家庭厨师", "家庭计划员", "生活百科", "家庭总管"} <= set(agents)
        for name in ("家庭厨师", "家庭计划员", "生活百科", "家庭总管"):
            version = db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agents[name].id))
            deployment = db.scalar(select(AgentDeployment).where(AgentDeployment.agent_version_id == version.id))
            installation = db.scalar(select(AgentInstallation).where(AgentInstallation.deployment_id == deployment.id))
            assert version.version == 1 and version.published_at is not None
            assert deployment.visibility == "official"
            assert installation.status == "installed" and installation.authority_slot == "active"


def test_seed_is_idempotent_and_versions_immutable(client, app):
    from backend.app.db.seed import seed_database
    from backend.app.db.models import Account, AgentVersion, HouseholdMember

    seed_database(app.state.database.session_factory, app.state.settings)
    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        agents = _agents(db, household_id)
        before = {name: db.scalars(select(AgentVersion.definition_json).where(
            AgentVersion.agent_id == row.id)).all() for name, row in agents.items()}
    seed_database(app.state.database.session_factory, app.state.settings)
    seed_database(app.state.database.session_factory, app.state.settings)
    with app.state.database.session_factory() as db:
        after = {name: db.scalars(select(AgentVersion.definition_json).where(
            AgentVersion.agent_id == row.id)).all() for name, row in _agents(db, household_id).items()}
        assert before == after
        for name in ("家庭厨师", "家庭计划员", "生活百科", "家庭总管"):
            assert db.scalar(select(func.count()).select_from(AgentVersion).where(
                AgentVersion.agent_id == _agents(db, household_id)[name].id)) == 1


def test_least_privilege_default_grants_exclude_reversible_writes(client, app):
    from backend.app.db.models import (Account, AgentDefinition, AgentDeployment, AgentInstallation,
                                       AgentVersion, HouseholdMember, InstallationPermission, ToolDefinition)

    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        agents = _agents(db, household_id)
        for name in ("家庭厨师", "家庭计划员", "生活百科"):
            version = db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agents[name].id))
            deployment = db.scalar(select(AgentDeployment).where(AgentDeployment.agent_version_id == version.id))
            installation = db.scalar(select(AgentInstallation).where(AgentInstallation.deployment_id == deployment.id))
            granted = set(db.scalars(select(ToolDefinition.key).join(InstallationPermission,
                InstallationPermission.tool_id == ToolDefinition.id).where(
                    InstallationPermission.installation_id == installation.id)).all())
            reversible = set(db.scalars(select(ToolDefinition.key).where(
                ToolDefinition.risk_level == "reversible")).all())
            assert granted.isdisjoint(reversible | {key for key in granted if key.startswith("device.")}), name
            assert granted, f"{name} 应有默认只读授权"
        encyclopedia_version = db.scalar(select(AgentVersion).where(
            AgentVersion.agent_id == agents["生活百科"].id))
        enc_deployment = db.scalar(select(AgentDeployment).where(
            AgentDeployment.agent_version_id == encyclopedia_version.id))
        enc_installation = db.scalar(select(AgentInstallation).where(
            AgentInstallation.deployment_id == enc_deployment.id))
        granted = set(db.scalars(select(ToolDefinition.key).join(InstallationPermission,
            InstallationPermission.tool_id == ToolDefinition.id).where(
                InstallationPermission.installation_id == enc_installation.id)).all())
        assert granted == {"household.knowledge.search"}


def test_steward_delegates_to_installed_official_children(client, app):
    from backend.app.db.models import AgentDefinition, Account, AgentDeployment, AgentVersion, HouseholdMember

    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        agents = _agents(db, household_id)
        steward_version = db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agents["家庭总管"].id))
        definition = json.loads(steward_version.definition_json)
        children = set(definition["child_deployments"])
        deployments = db.scalars(select(AgentDeployment).where(
            AgentDeployment.household_id == household_id)).all()
        by_agent = {}
        for deployment in deployments:
            version = db.get(AgentVersion, deployment.agent_version_id)
            by_agent[db.get(AgentDefinition, version.agent_id).name] = deployment.id
        assert children == {by_agent[name] for name in ("设备管家", "家庭厨师", "家庭计划员", "生活百科")}
        assert definition["budgets"]["delegation_depth"] >= 1
        assert definition["permissions"] == []


def test_household_agent_catalog_seeded_as_tool_definitions(client, app):
    from backend.app.db.models import ToolDefinition

    with app.state.database.session_factory() as db:
        keys = set(db.scalars(select(ToolDefinition.key).where(
            ToolDefinition.key.like("household.%"))).all())
        assert {"household.shopping.write", "household.reminder.create", "household.preference.propose",
                "household.knowledge.search", "household.recipe.scale"} <= keys
