# -*- coding: utf-8 -*-
"""Phase 8 operational overview API: every metric computed from real records
with an explicit time window via grouped queries; admin-only and audited."""
import json
from datetime import timedelta

from sqlalchemy import select

from backend.tests.test_audit_query_api import _admin_headers, _user_headers
from backend.tests.test_celery_tasks import _home
from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime


def _register_household(app, name="增长家庭"):
    import uuid

    from backend.app.db.models import Account, Household, HouseholdMember

    with app.state.database.session_factory() as db:
        account = Account(username=f"ops-{uuid.uuid4().hex[:10]}", password_hash="h")
        db.add(account)
        db.flush()
        household = Household(name=name, created_by_account_id=account.id)
        db.add(household)
        db.flush()
        db.add(HouseholdMember(household_id=household.id, account_id=account.id, role="owner"))
        db.commit()
        return household.id


def test_overview_metrics_reconcile_exactly_with_real_records(client, app, monkeypatch):
    from backend.app.db.base import utcnow
    from backend.app.db.models import (AgentToolBinding, AgentVersion, CeleryTaskRecord, HouseholdTask,
                                       ModelUsage, QuotaIncident, ReviewSubmission, ToolDefinition, ToolExecution)

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    new_household = _register_household(app)
    headers = _admin_headers(client)

    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id)
        model_id = model.id
        month = utcnow().strftime("%Y-%m")
        db.add(ModelUsage(household_id=household_id, model_id=model_id, month=month,
                          tokens=30, reserved_tokens=30, status="completed"))
        db.add(ModelUsage(household_id=household_id, model_id=model_id, month=month,
                          tokens=8, reserved_tokens=8, status="failed"))
        db.commit()
        db.add(QuotaIncident(household_id=household_id, model_id=model_id, code="HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"))
        db.commit()

        high_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.risk_level == "high"))
        db.add(CeleryTaskRecord(idempotency_key="ops-overview-1", task_type="demo.task",
                                household_id=household_id, status="succeeded", attempt=1))
        db.add(CeleryTaskRecord(idempotency_key="ops-overview-2", task_type="demo.task",
                                household_id=household_id, status="dead", attempt=3,
                                error_code="UPSTREAM_UNAVAILABLE"))
        db.commit()

    from backend.tests.test_audit_query_api import _seed_runs

    _seed_runs(app, household_id, count=5)

    from backend.app.db.models import AgentRun

    with app.state.database.session_factory() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.household_id == household_id))
        tool = db.scalar(select(ToolDefinition).limit(1))
        db.add(ToolExecution(household_id=household_id, run_id=run.id, tool_id=tool.id,
                             principal_id=account_id, agent_version_id=run.agent_version_id, tool_version="1",
                             model_id=model_id, deployment_id=run.deployment_id, deployment_revision=1,
                             status="permission_denied", error_code="PERMISSION_REQUIRED",
                             idempotency_key="ops-denied-1", arguments_json="{}", result_json="{}",
                             started_at=utcnow(), finished_at=utcnow(), duration_ms=5))
        # 审核队列深度与周转：一条待审 + 一条已通过（含高风险工具绑定）
        definition = db.scalar(select(AgentVersion).where(AgentVersion.household_id == household_id))
        db.add(ReviewSubmission(household_id=household_id, agent_version_id=definition.id,
                                draft_id="d-1", status="review_pending", created_at=utcnow() - timedelta(hours=2)))
        db.add(ReviewSubmission(household_id=household_id, agent_version_id=definition.id,
                                draft_id="d-2", status="approved", created_at=utcnow() - timedelta(hours=4),
                                decided_at=utcnow() - timedelta(hours=2), decided_by_account_id=account_id))
        db.add(AgentToolBinding(household_id=household_id, agent_version_id=definition.id, tool_id=high_tool.id))
        db.commit()

    response = client.get("/api/ops/overview?window_days=7", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["window_days"] == 7
    assert body["households"]["total"] >= 3
    assert body["households"]["new_in_window"] >= 1
    assert body["households"]["growth_by_day"]

    assert body["installations"]["active"] >= 1  # 官方设备链安装
    assert body["installations"]["households_with_active"] >= 1

    assert body["agent_runs"]["total_in_window"] == 5
    assert body["agent_runs"]["by_status"] == {"completed": 4, "failed": 1}

    assert body["tool_executions"]["total_in_window"] == 1
    assert body["tool_executions"]["permission_denials"] == 1

    assert body["celery_tasks"]["by_status"] == {"succeeded": 1, "dead": 1}
    assert body["celery_tasks"]["failure_rate"] == 0.5

    assert body["review"]["queue_depth"] == 1
    assert body["review"]["decided_in_window"] == 1
    assert 7100 <= body["review"]["avg_turnaround_seconds"] <= 7300  # 2 小时 ± 抖动

    assert body["model_usage"]["requests"] == 2 and body["model_usage"]["tokens"] == 38
    assert body["model_usage"]["by_purpose"][0]["purpose"] == "primary_chat"
    assert body["model_usage"]["by_model"][0]["model_id"] == model_id

    assert body["quota_incidents"]["count"] == 1
    assert body["quota_incidents"]["by_code"] == {"HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED": 1}

    assert body["risk"]["high_risk_approvals"] == 1
    assert body["risk"]["permission_denials"] == 1


def test_overview_requires_admin_and_window_is_validated(client, app):
    assert client.get("/api/ops/overview").status_code == 401
    assert client.get("/api/ops/overview", headers=_user_headers(client)).status_code == 403
    headers = _admin_headers(client)
    assert client.get("/api/ops/overview?window_days=0", headers=headers).status_code == 422
    assert client.get("/api/ops/overview?window_days=400", headers=headers).status_code == 422


def test_overview_read_is_audited(client, app):
    from backend.app.db.models import Account, AdminActionAudit

    headers = _admin_headers(client)
    client.get("/api/ops/overview?window_days=7", headers=headers)
    with app.state.database.session_factory() as db:
        admin = db.scalar(select(Account).where(Account.username == "admin"))
        rows = db.scalars(select(AdminActionAudit).where(AdminActionAudit.action == "ops.read")).all()
        assert any(row.principal_id == admin.id and row.resource_id == "/api/ops/overview"
                   and json.loads(row.detail_json)["filters"]["window_days"] == 7 for row in rows)


def test_task_type_catalog_supports_manual_trigger_boundary(client, app):
    headers = _admin_headers(client)
    catalog = client.get("/api/ops/tasks/types", headers=headers)
    assert catalog.status_code == 200, catalog.text
    items = {item["task_type"]: item for item in catalog.json()["items"]}
    assert "household.reminders.dispatch" in items
    assert items["household.reminders.dispatch"]["max_attempts"] >= 1
    assert items["creator.sandbox_trial"]["description"]
    assert client.get("/api/ops/tasks/types", headers=_user_headers(client)).status_code == 403
