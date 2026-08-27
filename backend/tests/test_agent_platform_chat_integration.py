import inspect
from backend.app.db.base import utcnow


def test_builtin_tool_module_no_longer_reads_raw_session_factory():
    from backend.agent.tools import agent_tools

    source = inspect.getsource(agent_tools)
    assert "session_factory" not in source
    assert "runtime.context.get(\"repositories\")" in source


def test_chat_builds_typed_platform_context_without_breaking_sse(client, app):
    from backend.agent.platform.context import RunContext
    from backend.app.db.models import AgentDefinition, AgentDeployment, AgentInstallation, AgentRun, AgentVersion, ChatSession, HouseholdMember

    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = client.post("/api/chat/sessions", headers=headers, json={"title": "platform"})
    session_id = created.json()["id"]
    with app.state.database.session_factory.begin() as db:
        member = db.query(HouseholdMember).filter_by(account_id=login.json()["account"]["id"]).one()
        definition = AgentDefinition(household_id=member.household_id, name="Chat Platform Agent")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
                               definition_json='{"prompt":"chat","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush()
        db.add(AgentInstallation(household_id=member.household_id, deployment_id=deployment.id,
                                 agent_version_id=version.id, status="installed"))
        chat_session = db.get(ChatSession, session_id)
        chat_session.deployment_id = deployment.id

    captured = []

    class Runner:
        def stream(self, _messages, context):
            captured.append(context)
            yield {"event": "run", "data": {"status": "started"}}
            yield {"event": "token", "data": {"content": "platform ok"}}

    app.state.agent_runner = Runner()
    response = client.post(f"/api/chat/sessions/{session_id}/chat", headers=headers, json={"message": "hello"})

    assert response.status_code == 200
    assert isinstance(captured[0], RunContext)
    assert captured[0].deployment_id == deployment.id
    assert not hasattr(captured[0], "session_factory")
    with app.state.database.session_factory() as db:
        run = db.get(AgentRun, captured[0].run_id)
        assert run.agent_version_id == version.id
