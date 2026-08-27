import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register(client, username: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Password@123", "city": "杭州"},
    )
    assert response.status_code == 201
    return response.json()


def test_registration_creates_default_household_and_owner_membership(client, app):
    from backend.app.db.models import Account, Household, HouseholdMember

    account_body = register(client, "orbit-owner")["account"]

    with app.state.database.session_factory() as session:
        account = session.get(Account, account_body["id"])
        membership = session.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account.id))
        household = session.get(Household, membership.household_id)

    assert membership.role == "owner"
    assert household.name == "orbit-owner的家庭"


def test_seeded_admin_has_a_default_household(client, app):
    from backend.app.db.models import Account, HouseholdMember

    with app.state.database.session_factory() as session:
        admin = session.scalar(select(Account).where(Account.username == "admin"))
        membership = session.scalar(select(HouseholdMember).where(HouseholdMember.account_id == admin.id))

    assert membership.role == "owner"


def test_household_invitation_acceptance_moves_account_into_inviter_household(client):
    owner = register(client, "home-owner")
    guest = register(client, "home-guest")

    current = client.get("/api/households/current", headers=bearer(owner["access_token"])).json()
    invitation = client.post(
        f"/api/households/{current['id']}/invitations",
        headers=bearer(owner["access_token"]),
        json={"username": "home-guest", "role": "member"},
    )
    assert invitation.status_code == 201

    accepted = client.post(
        f"/api/households/invitations/{invitation.json()['id']}/accept",
        headers=bearer(guest["access_token"]),
    )
    assert accepted.status_code == 200
    assert accepted.json()["household_id"] == current["id"]
    assert accepted.json()["role"] == "member"
    assert client.post(
        f"/api/households/{current['id']}/invitations",
        headers=bearer(guest["access_token"]),
        json={"username": "home-owner", "role": "viewer"},
    ).status_code == 403


def test_household_role_rules_and_cross_household_isolation(client):
    owner = register(client, "role-owner")
    outsider = register(client, "role-outsider")
    household = client.get("/api/households/current", headers=bearer(owner["access_token"])).json()

    assert client.get(
        f"/api/households/{household['id']}", headers=bearer(outsider["access_token"])
    ).status_code == 404
    assert client.post(
        f"/api/households/{household['id']}/invitations",
        headers=bearer(outsider["access_token"]),
        json={"username": "role-owner", "role": "viewer"},
    ).status_code == 404


def test_setup_status_is_stable_and_non_ai_household_api_remains_available(client):
    owner = register(client, "setup-owner")
    headers = bearer(owner["access_token"])

    status = client.get("/api/setup/status", headers=headers)
    household = client.get("/api/households/current", headers=headers)

    assert status.status_code == 200
    assert status.json() == {
        "ready": False,
        "error_code": "MODEL_NOT_CONFIGURED",
        "required_purposes": ["primary_chat", "embedding"],
        "configured_purposes": [],
        "missing_purposes": ["primary_chat", "embedding"],
        "can_configure": False,
        "master_key_valid": False,
    }
    assert household.status_code == 200


def test_home_orbit_nodes_use_current_household_state(client):
    owner = register(client, "orbit-state")
    response = client.get("/api/home/orbit", headers=bearer(owner["access_token"]))

    assert response.status_code == 200
    assert response.json()["nodes"] == [
        {
            "id": "setup",
            "label": "完成模型初始化",
            "kind": "setup",
            "status": "attention",
            "href": "/admin/setup",
            "detail": "主聊天与向量模型尚未配置",
        },
        {
            "id": "household",
            "label": "家庭空间",
            "kind": "household",
            "status": "ready",
            "href": "/household",
            "detail": "1 位家庭成员",
        },
    ]


def test_v2_schema_domains_and_alembic_version_table_exist(settings):
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    tables = set(inspect(database.engine).get_table_names())

    assert {
        "households",
        "household_members",
        "household_preferences",
        "invitations",
        "llm_endpoints",
        "llm_credentials",
        "llm_models",
        "model_purpose_bindings",
        "skills",
        "skill_versions",
        "agent_definitions",
        "agent_versions",
        "agent_deployments",
        "agent_runs",
        "tool_executions",
        "household_tasks",
        "notifications",
    } <= tables


def test_tenant_tables_have_non_nullable_household_ownership_path():
    from backend.app.db.models import Base

    platform_global = {
        "accounts", "users", "devices", "inventory", "knowledge_files", "faq_pairs",
        "llm_endpoints", "llm_credentials", "llm_models", "model_purpose_bindings",
        "model_health_checks", "tool_definitions",
    }
    tenant_tables = {
        "household_preferences", "invitations", "model_grants", "usage_quotas", "tool_permissions",
        "skills", "skill_versions", "skill_knowledge_assets", "agent_definitions", "agent_versions",
        "agent_skill_bindings", "agent_tool_bindings", "agent_deployments", "agent_installations",
        "installation_permissions", "favorites", "creator_drafts", "review_submissions", "review_checks",
        "review_decisions", "reports", "agent_runs", "tool_executions", "approval_requests",
        "household_tasks", "task_schedules", "notifications", "notification_reads",
    }
    tables = Base.metadata.tables

    def reaches_household(table_name: str, seen: set[str] | None = None) -> bool:
        if table_name == "households":
            return True
        seen = (seen or set()) | {table_name}
        for fk in tables[table_name].foreign_keys:
            if fk.parent.nullable or fk.column.table.name in seen:
                continue
            if reaches_household(fk.column.table.name, seen):
                return True
        return False

    assert platform_global.isdisjoint(tenant_tables)
    assert tenant_tables <= set(tables)
    assert {name for name in tenant_tables if not reaches_household(name)} == set()


def test_version_and_deployment_relationship_constraints_are_enforced():
    from backend.app.db.models import Base

    tables = Base.metadata.tables
    skill_unique = {tuple(column.name for column in constraint.columns) for constraint in tables["skill_versions"].constraints if hasattr(constraint, "columns")}
    agent_unique = {tuple(column.name for column in constraint.columns) for constraint in tables["agent_versions"].constraints if hasattr(constraint, "columns")}
    deployment_fks = {(fk.parent.name, fk.column.table.name, fk.column.name) for fk in tables["agent_deployments"].foreign_keys}

    assert ("skill_id", "version") in skill_unique
    assert ("agent_id", "version") in agent_unique
    assert ("household_id", "households", "id") in deployment_fks
    assert ("agent_version_id", "agent_versions", "id") in deployment_fks
    assert tables["agent_deployments"].c.household_id.nullable is False


def test_database_rejects_cross_household_agent_skill_binding(settings):
    from backend.app.db.session import create_database
    from backend.app.db.models import Account, Household
    from backend.app.db.platform_models import AgentDefinition, AgentSkillBinding, AgentVersion, Skill, SkillVersion

    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        owner = Account(username="tenant-owner", password_hash="hash")
        session.add(owner)
        session.flush()
        household_a = Household(name="A", created_by_account_id=owner.id)
        household_b = Household(name="B", created_by_account_id=owner.id)
        session.add_all([household_a, household_b])
        session.flush()
        agent = AgentDefinition(household_id=household_a.id, name="A agent")
        skill = Skill(household_id=household_b.id, name="B skill")
        session.add_all([agent, skill])
        session.flush()
        agent_version = AgentVersion(household_id=household_a.id, agent_id=agent.id, version=1)
        skill_version = SkillVersion(household_id=household_b.id, skill_id=skill.id, version=1)
        session.add_all([agent_version, skill_version])
        session.flush()
        session.add(AgentSkillBinding(
            household_id=household_a.id,
            agent_version_id=agent_version.id,
            skill_version_id=skill_version.id,
        ))

        with pytest.raises(IntegrityError):
            session.commit()


def test_database_rejects_cross_household_agent_deployment_version(settings):
    from backend.app.db.models import Account, Household
    from backend.app.db.platform_models import AgentDefinition, AgentDeployment, AgentVersion
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        owner = Account(username="deployment-version-owner", password_hash="hash")
        session.add(owner)
        session.flush()
        household_a = Household(name="A", created_by_account_id=owner.id)
        household_b = Household(name="B", created_by_account_id=owner.id)
        session.add_all([household_a, household_b])
        session.flush()
        agent = AgentDefinition(household_id=household_a.id, name="A agent")
        session.add(agent)
        session.flush()
        version = AgentVersion(household_id=household_a.id, agent_id=agent.id, version=1)
        session.add(version)
        session.flush()
        session.add(AgentDeployment(household_id=household_b.id, agent_version_id=version.id))

        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("resource_name", ["installation", "favorite", "report", "run"])
def test_database_rejects_cross_household_deployment_relationships(settings, resource_name):
    from backend.app.db.models import Account, Household
    from backend.app.db.platform_models import (
        AgentDefinition, AgentDeployment, AgentInstallation, AgentRun, AgentVersion, Favorite, Report,
    )
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        owner = Account(username=f"deployment-{resource_name}", password_hash="hash")
        session.add(owner)
        session.flush()
        household_a = Household(name="A", created_by_account_id=owner.id)
        household_b = Household(name="B", created_by_account_id=owner.id)
        session.add_all([household_a, household_b])
        session.flush()
        agent = AgentDefinition(household_id=household_a.id, name="A agent")
        session.add(agent)
        session.flush()
        version = AgentVersion(household_id=household_a.id, agent_id=agent.id, version=1)
        session.add(version)
        session.flush()
        deployment = AgentDeployment(household_id=household_a.id, agent_version_id=version.id)
        session.add(deployment)
        session.flush()
        resource_type = {
            "installation": AgentInstallation,
            "favorite": Favorite,
            "report": Report,
            "run": AgentRun,
        }[resource_name]
        session.add(resource_type(household_id=household_b.id, deployment_id=deployment.id))

        with pytest.raises(IntegrityError):
            session.commit()


def test_empty_database_upgrades_through_frozen_alembic_chain(tmp_path):
    database_path = tmp_path / "alembic-empty.db"
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(engine)
    assert inspector.get_table_names()
    assert inspector.get_columns("agent_deployments")
    assert next(column for column in inspector.get_columns("agent_deployments") if column["name"] == "household_id")["nullable"] is False
    assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("agent_deployments")} == {"households", "agent_versions"}
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "20260827_0008"


def test_reset_script_requires_explicit_confirmation(tmp_path):
    database_path = tmp_path / "protected.db"
    database_path.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.reset_database", "--database-url", f"sqlite:///{database_path}"],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "BACK UP THE DATABASE BEFORE CONTINUING" in result.stderr
    assert database_path.read_text(encoding="utf-8") == "keep"
