# -*- coding: utf-8 -*-
"""Phase 8 audit query APIs (/api/audit/*): admin-only, paginated, filtered,
indexed read paths whose access is itself audited. Household users get stable
403; unknown detail ids get stable 404; bodies never contain secrets."""
import json
from datetime import timedelta

from sqlalchemy import select

from backend.tests.test_celery_tasks import _home


def _admin_headers(client):
    token = client.post("/api/auth/login", json={"username": "admin",
                                                 "password": "AdminTest@123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _user_headers(client):
    token = client.post("/api/auth/login", json={"username": "demo",
                                                "password": "DemoTest@123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_runs(app, household_id, count=25, model_id="model-a"):
    from backend.app.db.models import AgentDefinition, AgentDeployment, AgentRun, AgentVersion, HouseholdMember

    with app.state.database.session_factory.begin() as db:
        member_id = db.scalar(select(HouseholdMember.id).where(HouseholdMember.household_id == household_id))
        principal_id = db.scalar(select(HouseholdMember.account_id).where(HouseholdMember.id == member_id))
        definition = AgentDefinition(household_id=household_id, name="审计探针")
        db.add(definition)
        db.flush()
        version = AgentVersion(household_id=household_id, agent_id=definition.id, version=1)
        db.add(version)
        db.flush()
        deployment = AgentDeployment(household_id=household_id, agent_version_id=version.id,
                                     visibility="private")
        db.add(deployment)
        db.flush()
        for index in range(count):
            db.add(AgentRun(household_id=household_id, deployment_id=deployment.id, principal_id=principal_id,
                            member_id=member_id, session_id=f"s-{index}", agent_version_id=version.id,
                            deployment_revision=deployment.revision,
                            model_id=model_id if index % 2 == 0 else "model-b",
                            model_revision=1, status="completed" if index % 5 else "failed",
                            error_code="RUN_FAILED" if index % 5 == 0 else None))
    return deployment.id


def test_agent_runs_pagination_filters_and_time_window(client, app):
    account_id, household_id = _home(app)
    deployment_id = _seed_runs(app, household_id)
    headers = _admin_headers(client)

    page1 = client.get("/api/audit/agent-runs?page=1&page_size=10", headers=headers)
    assert page1.status_code == 200, page1.text
    body = page1.json()
    assert body["total"] == 25 and len(body["items"]) == 10 and body["page"] == 1 and body["page_size"] == 10
    page3 = client.get("/api/audit/agent-runs?page=3&page_size=10", headers=headers).json()
    assert len(page3["items"]) == 5

    filtered = client.get("/api/audit/agent-runs?status=failed&page_size=100", headers=headers).json()
    assert filtered["total"] == 5 and all(item["status"] == "failed" for item in filtered["items"])
    by_model = client.get("/api/audit/agent-runs?model_id=model-a&page_size=100", headers=headers).json()
    assert by_model["total"] == 13 and all(item["model_id"] == "model-a" for item in by_model["items"])
    by_deployment = client.get(f"/api/audit/agent-runs?deployment_id={deployment_id}", headers=headers).json()
    assert by_deployment["total"] == 25
    by_household = client.get(f"/api/audit/agent-runs?household_id={household_id}", headers=headers).json()
    assert by_household["total"] == 25
    other_household = client.get("/api/audit/agent-runs?household_id=missing-household", headers=headers).json()
    assert other_household["total"] == 0

    from backend.app.db.base import utcnow

    future = (utcnow() + timedelta(days=1)).isoformat()
    none_hit = client.get(f"/api/audit/agent-runs?from={future}", headers=headers).json()
    assert none_hit["total"] == 0
    past = (utcnow() - timedelta(days=30)).isoformat()
    all_hit = client.get(f"/api/audit/agent-runs?from={past}&to={future}", headers=headers).json()
    assert all_hit["total"] == 25


def test_audit_access_requires_admin_and_is_itself_audited(client, app):
    from backend.app.db.models import Account, AdminActionAudit

    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    assert client.get("/api/audit/agent-runs").status_code == 401  # 未认证
    denied = client.get("/api/audit/agent-runs", headers=_user_headers(client))
    assert denied.status_code == 403  # 家庭用户稳定 403
    for path in ("/api/audit/tool-executions", "/api/audit/model-usage", "/api/audit/celery-tasks",
                 "/api/audit/admin-actions", "/api/audit/deployments/pointers"):
        assert client.get(path, headers=_user_headers(client)).status_code == 403

    client.get("/api/audit/agent-runs?status=failed&page=2", headers=headers)
    with app.state.database.session_factory() as db:
        admin = db.scalar(select(Account).where(Account.username == "admin"))
        reads = db.scalars(select(AdminActionAudit).where(AdminActionAudit.action == "audit.read")).all()
        assert any(row.principal_id == admin.id and row.resource_id == "/api/audit/agent-runs"
                   and json.loads(row.detail_json)["filters"]["status"] == "failed" for row in reads)


def test_tool_execution_listing_and_detail_with_redacted_bodies(client, app):
    from backend.app.db.models import ToolDefinition, ToolExecution

    account_id, household_id = _home(app)
    deployment_id = _seed_runs(app, household_id, count=1)
    headers = _admin_headers(client)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import AgentRun, HouseholdMember

        run = db.scalar(select(AgentRun).where(AgentRun.household_id == household_id))
        principal_id = db.scalar(select(HouseholdMember.account_id).where(
            HouseholdMember.household_id == household_id))
        tool = db.scalar(select(ToolDefinition).limit(1))
        from backend.app.db.base import utcnow as _utcnow

        started = _utcnow()
        db.add(ToolExecution(household_id=household_id, run_id=run.id, tool_id=tool.id, principal_id=principal_id,
                             agent_version_id=run.agent_version_id, tool_version="1", model_id="model-a",
                             deployment_id=deployment_id,
                             deployment_revision=1, status="permission_denied", error_code="PERMISSION_REQUIRED",
                             idempotency_key="audit-te-1", arguments_json='{"q": "[REDACTED]"}',
                             result_json="{}", started_at=started, finished_at=_utcnow(),
                             duration_ms=12, redaction_schema_version=1))
        db.commit()
        run_id = run.id
    listing = client.get("/api/audit/tool-executions?status=permission_denied", headers=headers).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["error_code"] == "PERMISSION_REQUIRED" and item["duration_ms"] is not None

    detail = client.get(f"/api/audit/tool-executions/{item['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["arguments"] == {"q": "[REDACTED]"}
    assert client.get("/api/audit/tool-executions/no-such-id", headers=headers).status_code == 404
    by_run = client.get(f"/api/audit/tool-executions?run_id={run_id}", headers=headers).json()
    assert by_run["total"] == 1
    by_tool = client.get(f"/api/audit/tool-executions?tool_id={tool.id}", headers=headers).json()
    assert by_tool["total"] == 1


def test_model_usage_filters_by_purpose_and_status(client, app):
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    from backend.app.db.models import ModelUsage
    from backend.app.db.base import utcnow

    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id)
        model_id = model.id
        db.add(ModelUsage(household_id=household_id, model_id=model_id, month=utcnow().strftime("%Y-%m"),
                          tokens=8, reserved_tokens=8, status="completed"))
        db.add(ModelUsage(household_id=household_id, model_id=model_id, month=utcnow().strftime("%Y-%m"),
                          tokens=8, reserved_tokens=8, status="reserved"))
        db.commit()

    listing = client.get("/api/audit/model-usage?household_id=%s&status=completed" % household_id,
                         headers=headers).json()
    assert listing["total"] == 1 and listing["items"][0]["model_id"] == model_id
    by_purpose = client.get("/api/audit/model-usage?purpose=primary_chat", headers=headers).json()
    assert by_purpose["total"] == 2  # 绑定到 primary_chat 的档案
    embedding = client.get("/api/audit/model-usage?purpose=embedding", headers=headers).json()
    assert embedding["total"] == 0


def test_model_usage_accounting_reconciles_and_surfaces_unreconciled(client, app):
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    from backend.app.db.models import ModelUsage
    from backend.app.db.base import utcnow

    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id)
        model_id = model.id
        month = utcnow().strftime("%Y-%m")
        db.add(ModelUsage(household_id=household_id, model_id=model_id, month=month,
                          tokens=8, reserved_tokens=8, status="completed"))
        stale = ModelUsage(household_id=household_id, model_id=model_id, month=month,
                           tokens=8, reserved_tokens=8, status="reserved")
        stale.created_at = utcnow() - timedelta(hours=2)
        db.add(stale)
        db.commit()
        stale_id = stale.id
    accounting = client.get("/api/audit/model-usage/accounting?days=7", headers=headers)
    assert accounting.status_code == 200, accounting.text
    body = accounting.json()
    assert body["by_status"] == {"completed": 1, "reserved": 1}
    assert body["unreconciled"]["count"] == 1
    assert body["unreconciled"]["items"][0]["id"] == stale_id
    assert body["totals"]["requests"] == 2 and body["totals"]["tokens"] == 16
    assert body["by_model"][0]["model_id"] == model_id and body["by_model"][0]["requests"] == 2
    assert body["by_purpose"][0]["purpose"] == "primary_chat"
    assert len(body["by_day"]) >= 1


def test_celery_task_listing_detail_and_failure_notifications(client, app):
    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    from backend.app.db.base import utcnow
    from backend.app.tasks.executor import TaskExecutor
    from backend.tests.test_celery_tasks import FlakyHandler

    executor = TaskExecutor(app.state.database.session_factory,
                            handlers={"demo.broken": FlakyHandler(failures=99)}, defaults={"max_attempts": 2})
    executor.submit("demo.broken", {"household_id": household_id}, "audit-dead-1", household_id=household_id)
    executor.run("audit-dead-1")
    executor.run("audit-dead-1", now=utcnow() + timedelta(minutes=10))

    listing = client.get("/api/audit/celery-tasks?status=dead", headers=headers).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["idempotency_key"] == "audit-dead-1" and item["error_code"] == "UPSTREAM_UNAVAILABLE"

    detail = client.get(f"/api/audit/celery-tasks/{item['id']}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["attempt"] == 2 and body["max_attempts"] == 2
    assert body["audit"]["failures"] and body["audit"]["failures"][0]["code"] == "UPSTREAM_UNAVAILABLE"
    assert body["notifications"] and body["notifications"][0]["kind"] == "task_failed"
    assert client.get("/api/audit/celery-tasks/no-such-task", headers=headers).status_code == 404
    by_type = client.get("/api/audit/celery-tasks?task_type=demo.broken", headers=headers).json()
    assert by_type["total"] == 1
    by_error = client.get("/api/audit/celery-tasks?error_code=UPSTREAM_UNAVAILABLE", headers=headers).json()
    assert by_error["total"] == 1


def test_admin_action_listing_filters_and_actor_labels(client, app):
    from backend.app.db.models import Account, AdminActionAudit

    headers = _admin_headers(client)
    with app.state.database.session_factory.begin() as db:
        admin = db.scalar(select(Account).where(Account.username == "admin"))
        db.add(AdminActionAudit(principal_id=admin.id, household_id=None, action="review.approve",
                                resource_type="review_submission", resource_id="sub-1",
                                detail_json='{"reason": "通过"}'))
    listing = client.get("/api/audit/admin-actions?action=review.approve", headers=headers).json()
    assert listing["total"] >= 1
    item = next(row for row in listing["items"] if row["resource_id"] == "sub-1")
    assert item["actor_username"] == "admin"
    assert item["detail"] == {"reason": "通过"}
    by_actor = client.get(f"/api/audit/admin-actions?actor_id={admin.id}", headers=headers).json()
    assert by_actor["total"] >= 1
    by_resource = client.get("/api/audit/admin-actions?resource_type=review_submission", headers=headers).json()
    assert any(row["resource_id"] == "sub-1" for row in by_resource["items"])
