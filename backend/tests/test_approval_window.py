"""审批窗口的服务端约束，以及审批列表给前端 UI 用的字段。

审批是「高风险工具逐次确认」的唯一闸门。`expires_at` 原来由客户端随意指定，
批一个十年窗口就等于把这道闸门一次性打开到底，所以窗口必须由服务端夹取。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.app.db.base import utcnow
from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


def _login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _pending_approval(app, account_id, *, key="platform.test_high_risk", arguments="{}"):
    """在 demo 的 household 里造一条 pending 审批，返回 (approval_id, tool_id, run 的 session_id)。"""
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentRun, AgentVersion, ApprovalRequest,
                                       HouseholdMember, ToolDefinition)

    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account_id))
        definition = AgentDefinition(household_id=member.household_id, name=f"approval-window-{key}")
        db.add(definition)
        db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
                               definition_json='{"prompt":"x","tools":[]}', published_at=utcnow())
        db.add(version)
        db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment)
        db.flush()
        run = AgentRun(household_id=member.household_id, deployment_id=deployment.id, principal_id=account_id,
                       member_id=member.id, session_id=f"window-session-{key}", agent_version_id=version.id,
                       deployment_revision=1, model_id="model", model_revision=1)
        db.add(run)
        db.flush()
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == key))
        approval = ApprovalRequest(household_id=member.household_id, run_id=run.id, principal_id=account_id,
                                   tool_id=tool.id, tool_version="1", deployment_id=deployment.id,
                                   deployment_revision=1, agent_version_id=version.id, arguments_digest="digest",
                                   arguments_json=arguments, idempotency_key=f"window-{key}",
                                   consent_digest="consent")
        db.add(approval)
        db.flush()
        return approval.id, tool.id, run.session_id


def test_approval_window_is_capped_server_side(client, app, settings):
    headers = _login(client, "demo", settings.demo_initial_password)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import Account

        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    approval_id, _tool_id, _session_id = _pending_approval(app, account_id)

    # 客户端申请十年：服务端必须夹到 approval_max_minutes 以内
    requested = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
    response = client.post(f"/api/approvals/{approval_id}/approve", headers=headers, json={"expires_at": requested})

    assert response.status_code == 200
    granted = datetime.fromisoformat(response.json()["expires_at"])
    limit = utcnow() + timedelta(minutes=settings.approval_max_minutes, seconds=1)
    assert granted <= limit, "审批窗口不能被客户端拉长到超过服务端上限"


def test_approval_window_can_be_shortened_by_the_client(client, app, settings):
    headers = _login(client, "demo", settings.demo_initial_password)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import Account

        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    approval_id, _tool_id, _session_id = _pending_approval(app, account_id, key="platform.current_month")

    short = utcnow() + timedelta(minutes=1)
    requested = short.replace(tzinfo=timezone.utc).isoformat()

    response = client.post(f"/api/approvals/{approval_id}/approve", headers=headers, json={"expires_at": requested})

    assert response.status_code == 200
    granted = datetime.fromisoformat(response.json()["expires_at"])
    # 用户主动要求更短是合理诉求，不该被服务端反而拉长到默认值
    assert granted <= short + timedelta(seconds=1)


def test_approval_without_expires_at_uses_the_server_default(client, app, settings):
    headers = _login(client, "demo", settings.demo_initial_password)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import Account

        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    approval_id, _tool_id, _session_id = _pending_approval(app, account_id, key="platform.context_probe")

    response = client.post(f"/api/approvals/{approval_id}/approve", headers=headers, json={})

    assert response.status_code == 200
    granted = datetime.fromisoformat(response.json()["expires_at"])
    expected = utcnow() + timedelta(minutes=settings.approval_default_minutes, seconds=1)
    assert granted <= expected


def test_approval_in_the_past_is_still_rejected(client, app, settings):
    headers = _login(client, "demo", settings.demo_initial_password)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import Account

        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    approval_id, _tool_id, _session_id = _pending_approval(app, account_id, key="platform.workflow_confirm")

    response = client.post(f"/api/approvals/{approval_id}/approve", headers=headers,
                           json={"expires_at": (utcnow() - timedelta(minutes=1)).isoformat()})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "APPROVAL_EXPIRED"


def test_pending_approvals_carry_what_the_ui_needs(client, app, settings):
    """列表要能直接渲染审批卡：工具名、风险等级、脱敏后的参数、所属会话。"""
    headers = _login(client, "demo", settings.demo_initial_password)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import Account

        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    approval_id, tool_id, session_id = _pending_approval(
        app, account_id, arguments='{"value": "清洗油烟机", "api_key": "[REDACTED]"}')

    pending = client.get("/api/approvals", headers=headers)

    assert pending.status_code == 200
    entry = next(item for item in pending.json() if item["id"] == approval_id)
    assert entry["tool_id"] == tool_id
    assert entry["tool_key"] == "platform.test_high_risk"
    assert entry["tool_display_name"], "审批卡要显示工具名，不能只给一个 id"
    assert entry["risk_level"] == "high"
    assert entry["arguments"] == {"value": "清洗油烟机", "api_key": "[REDACTED]"}
    assert entry["session_id"] == session_id, "approval_requests 没有 session_id，要从 run 上取"


def test_pending_approvals_tolerate_unparsable_arguments(client, app, settings):
    """历史数据里的 arguments_json 可能是空的或非对象，列表不能因此 500。"""
    headers = _login(client, "demo", settings.demo_initial_password)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import Account

        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    approval_id, _tool_id, _session_id = _pending_approval(app, account_id, key="platform.current_month",
                                                          arguments="not-json")

    pending = client.get("/api/approvals", headers=headers)

    assert pending.status_code == 200
    entry = next(item for item in pending.json() if item["id"] == approval_id)
    assert entry["arguments"] == {}
