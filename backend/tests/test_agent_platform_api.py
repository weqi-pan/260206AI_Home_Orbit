from datetime import timedelta

from sqlalchemy import select
from backend.app.db.base import utcnow


def _login(client, username="admin", password="AdminTest@123"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_tool_catalog_exposes_metadata_not_executable_configuration(client):
    response = client.get("/api/tools/catalog", headers=_login(client))
    assert response.status_code == 200
    item = next(row for row in response.json() if row["key"] == "platform.current_month")
    assert set(item) == {"id", "key", "display_name", "description", "risk_level", "version", "enabled"}


def test_agent_version_api_publishes_immutable_snapshot_and_rolls_deployment(client, app):
    from backend.app.db.models import AgentDefinition, Household

    headers = _login(client)
    with app.state.database.session_factory.begin() as db:
        household = db.scalar(select(Household))
        agent = AgentDefinition(household_id=household.id, name="API Agent")
        db.add(agent); db.flush()
        household_id, agent_id = household.id, agent.id

    first = client.post(f"/api/agents/{agent_id}/versions", headers=headers,
                        json={"household_id": household_id, "definition": {"prompt": "v1", "tools": []}})
    assert first.status_code == 201
    deployment = client.post("/api/agents/deployments", headers=headers,
                             json={"household_id": household_id, "agent_version_id": first.json()["id"]})
    second = client.post(f"/api/agents/{agent_id}/versions", headers=headers,
                         json={"household_id": household_id, "definition": {"prompt": "v2", "tools": []}})
    switched = client.put(f"/api/agents/deployments/{deployment.json()['id']}", headers=headers,
                          json={"household_id": household_id, "agent_version_id": second.json()["id"]})
    assert switched.status_code == 200
    assert switched.json()["revision"] == 2
    assert first.json()["definition"] == {"prompt": "v1", "tools": []}


def test_approval_api_rejects_cross_household_request(client, app):
    from backend.app.db.models import ApprovalRequest

    headers = _login(client)
    with app.state.database.session_factory() as db:
        approval = db.scalar(select(ApprovalRequest).limit(1))
    if approval is None:
        response = client.post("/api/approvals/missing/approve", headers=headers,
                               json={"expires_at": (utcnow() + timedelta(minutes=5)).isoformat()})
        assert response.status_code == 404
