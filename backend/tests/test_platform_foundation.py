import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect, select


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
