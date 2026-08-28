# -*- coding: utf-8 -*-
"""Phase 8 governance E2E over real chains: chat run with tool execution,
sandbox trial with quota metering, celery reminder dispatch, review decision,
takedown, quota over-limit — then every audit/usage/dashboard number must
reconcile exactly against the database, the full admin decision chain
(setup -> grant -> review -> takedown) leaves a complete sanitized audit
trail, and household users never reach the audit endpoints (403)."""
import json
from datetime import timedelta

from sqlalchemy import func, select

from backend.tests.test_audit_integrity_sweep import SECRET
from backend.tests.test_celery_tasks import _home
from backend.tests.test_creator_sandbox import _grant_ceiling
from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime
from backend.tests.test_review_governance import MARKET_CARD, _login


def _admin(client):
    return _login(client, "admin", "AdminTest@123")


def _register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": "Chain@123",
                                                       "city": "上海"})
    assert response.status_code == 201, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["account"]["id"]


def _household_of(app, account_id):
    from backend.app.db.models import HouseholdMember

    with app.state.database.session_factory() as db:
        return db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account_id))


def _count(app, model, *conditions):
    with app.state.database.session_factory() as db:
        return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def test_governance_e2e_numbers_reconcile_and_audit_trail_is_complete(client, app, monkeypatch):
    from backend.app.db.base import utcnow
    from backend.app.db.models import (Account, AgentRun, AdminActionAudit, CeleryTaskRecord, CreatorDraft, Household,
                                       MarketEntry, ModelUsage, QuotaIncident, ReviewSubmission, ToolExecution)

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    creator_id, creator_household = _home(app)
    creator = _login(client)
    admin = _admin(client)

    # ---- 1. setup：管理员通过真实 API 配置模型（密钥只留掩码）
    endpoint = client.post("/api/models/endpoints", headers=admin, json={
        "protocol": "openai_compatible", "display_name": "E2E Gateway", "base_url": "https://api.openai.com/v1",
        "api_key": SECRET, "timeout_seconds": 30, "max_retries": 2, "enabled": True}).json()
    profile = client.post("/api/models/profiles", headers=admin, json={
        "endpoint_id": endpoint["id"], "model_name": "e2e-chat", "model_type": "chat", "max_tokens": 8,
        "requests_per_minute": 30, "agent_token_budget": 8, "enabled": True}).json()
    with app.state.database.session_factory() as db:
        from backend.app.db.models import LlmModel, ModelHealthCheck

        caps = {name: "passed" for name in ("generation", "streaming", "cancellation", "tool_calling",
                                            "structured_output")}
        row = db.get(LlmModel, profile["id"])
        row.capability_flags_json = json.dumps(caps)
        db.add(ModelHealthCheck(model_id=profile["id"], status="passed", capabilities_json=json.dumps(caps),
                                evidence_json="{}", endpoint_revision=endpoint["revision"],
                                model_revision=row.revision, created_at=utcnow()))
        db.commit()
    bound = client.put("/api/models/bindings/primary_chat", headers=admin,
                       json={"purpose": "primary_chat", "model_id": profile["id"]})
    assert bound.status_code == 200, bound.text
    with app.state.database.session_factory() as db:
        configured_runtime(db, creator_household, purpose="embedding")  # 平台就绪还需 embedding 绑定与创作者配额

    # ---- 2. grant：模型授权 + 工具授权（创作者家庭已具配额；买家家庭授权模型）
    granted_model = client.put(f"/api/models/households/{creator_household}/grants/{profile['id']}", headers=admin)
    assert granted_model.status_code == 200, granted_model.text
    _grant_ceiling(client, creator_household, "household.shopping.write")

    # ---- 3. review：草稿 -> 校验 -> 沙盒 -> 提交 -> 人工通过
    card = json.loads(json.dumps(MARKET_CARD, ensure_ascii=False))
    draft = client.post("/api/creator/drafts", headers=creator,
                        json={"title": "治理链草稿", "description": card["description"]}).json()
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=creator,
                        json={"intent_card": card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=creator).status_code == 200
    trial = client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=creator,
                        json={"granted_tool_keys": ["household.shopping.write"]})
    assert trial.status_code == 200 and trial.json()["status"] in {"succeeded", "completed"}, trial.text
    submitted = client.post(f"/api/creator/drafts/{draft['id']}/submit-review", headers=creator,
                            json={"category": "生活"})
    assert submitted.status_code == 201, submitted.text
    approved = client.post(f"/api/reviews/submissions/{submitted.json()['submission_id']}/decision",
                           headers=admin, json={"action": "approve", "reason": "治理链完整"})
    assert approved.status_code == 200, approved.text

    # ---- 4. 买家家庭安装并跑真实聊天链（含工具执行）
    buyer, buyer_account = _register(client, "gov-buyer")
    buyer_household = _household_of(app, buyer_account)
    with app.state.database.session_factory() as db:
        from backend.app.db.models import ModelGrant, UsageQuota

        db.add(ModelGrant(household_id=buyer_household, model_id=profile["id"]))
        if db.scalar(select(UsageQuota).where(UsageQuota.household_id == buyer_household)) is None:
            db.add(UsageQuota(household_id=buyer_household, monthly_limit=100, requests_per_minute=30,
                              agent_token_budget=8))
        db.commit()
    with app.state.database.session_factory() as db:
        entry = db.scalar(select(MarketEntry).where(MarketEntry.status == "published"))
        entry_id = entry.id
    install = client.post(f"/api/market/entries/{entry_id}/install", headers=buyer,
                          json={"granted_tool_keys": ["household.shopping.write"]}).json()
    session = client.post("/api/chat/sessions", headers=buyer,
                          json={"title": "治理会话", "deployment_id": install["deployment_id"]}).json()
    with client.stream("POST", f"/api/chat/sessions/{session['id']}/chat", headers=buyer,
                       json={"message": "加鸡蛋"}) as response:
        assert response.status_code == 200, response.text
        events = [line.split(":", 1)[1].strip() for line in response.iter_lines() if line.startswith("event:")]
    assert "run" in events
    messages = client.get(f"/api/chat/sessions/{session['id']}/messages", headers=buyer).json()
    assert "鸡蛋已加入清单" in messages[-1]["content"]
    with app.state.database.session_factory() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.deployment_id == install["deployment_id"]).order_by(
            AgentRun.created_at.desc()))
        assert run.status == "completed"
        run_id = run.id

    # ---- 5. 沙盒计量链：DeepAgent 路径产生预留/完成的 ModelUsage
    deep_card = {
        "name": "计量助手", "description": "计量沙盒", "prompt": "你是计量助手。",
        "examples": [], "model_requirements": {"capabilities": ["chat"]}, "skills": [], "tools": [],
        "budgets": {"tool_calls": 8, "tokens": 8000, "wall_time_seconds": 60, "delegation_depth": 0},
        "tests": [{"name": "计量", "input": "你好", "expected_contains": "8"}],
        "workflow": None,
    }
    from backend.tests.test_sandbox_quota_metering import _echo_model

    _echo_model(app, monkeypatch)
    from backend.app.creator.sandbox import SandboxTrialService

    with app.state.database.session_factory() as db:
        metered_draft = CreatorDraft(household_id=creator_household, title="计量草稿", status="validated",
                                     intent_card_json=json.dumps(deep_card, ensure_ascii=False))
        db.add(metered_draft)
        db.flush()
        metered_draft_id = metered_draft.id
        db.commit()
    service = SandboxTrialService(app.state.database.session_factory, creator_household, creator_id)
    report = service.create_report(metered_draft_id, [])
    metered = service.run(report.id)
    assert metered["status"] == "completed" and metered["cases"][0]["status"] == "passed", metered

    # ---- 6. celery 提醒派发链
    from backend.app.services.household_daily import HouseholdTaskService, ReminderScheduleService
    from backend.app.tasks.manual import beat_tick

    with app.state.database.session_factory.begin() as db:
        task = HouseholdTaskService(db).create(creator_household, title="浇花", account_id=creator_id)
        ReminderScheduleService(db).create(creator_household, task_id=task.id, interval_minutes=30,
                                           start_at=utcnow() - timedelta(minutes=45), account_id=creator_id)
    beat_tick(app.state.database.session_factory, now=utcnow())

    # ---- 7. 配额超限：调整月配额为 0（审计）→ 计量沙盒稳定失败并入事故史
    quota_set = client.put(f"/api/models/households/{creator_household}/quota", headers=admin,
                           json={"monthly_limit": 0, "requests_per_minute": 30, "agent_token_budget": 8})
    assert quota_set.status_code == 200, quota_set.text
    service2 = SandboxTrialService(app.state.database.session_factory, creator_household, creator_id)
    report2 = service2.create_report(metered_draft_id, [])
    over_limit = service2.run(report2.id)
    assert over_limit["cases"][0]["error_code"] == "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"

    # ---- 8. takedown：下架市场条目（治理闭环）
    taken_down = client.post(f"/api/reviews/entries/{entry_id}/takedown", headers=admin,
                             json={"reason": "治理链验证下架"})
    assert taken_down.status_code == 200, taken_down.text

    # ---- 9. 审计查询核对：run/tool/usage/task 全部落到真实行
    assert client.get(f"/api/audit/agent-runs?deployment_id={install['deployment_id']}",
                      headers=admin).json()["items"][0]["id"] == run_id
    tool_rows = client.get(f"/api/audit/tool-executions?run_id={run_id}&status=success",
                           headers=admin).json()
    assert tool_rows["total"] == 1  # 聊天链中的购物清单写入
    usage = client.get(f"/api/audit/model-usage?household_id={creator_household}", headers=admin).json()
    metered_rows = [item for item in usage["items"] if item["status"] == "completed"]
    assert len(metered_rows) >= 1
    tasks = client.get("/api/audit/celery-tasks?task_type=household.reminders.dispatch",
                       headers=admin).json()
    assert tasks["total"] >= 1 and all(item["status"] == "succeeded" for item in tasks["items"])
    detail = client.get(f"/api/audit/celery-tasks/{tasks['items'][0]['id']}", headers=admin).json()
    assert detail["audit"]["finished_at"]

    # ---- 10. 仪表盘数字与数据库精确对账
    overview = client.get("/api/ops/overview?window_days=1", headers=admin).json()
    since = utcnow() - timedelta(days=1)
    assert overview["households"]["total"] == _count(app, Household)
    assert overview["households"]["new_in_window"] == _count(app, Household, Household.created_at >= since)
    assert overview["agent_runs"]["total_in_window"] == _count(app, AgentRun, AgentRun.created_at >= since)
    assert overview["tool_executions"]["total_in_window"] == _count(app, ToolExecution,
                                                                    ToolExecution.started_at >= since)
    assert overview["model_usage"]["requests"] == _count(app, ModelUsage, ModelUsage.created_at >= since)
    assert overview["model_usage"]["tokens"] == (app.state.database.session_factory and _sum_tokens(app, since))
    assert overview["celery_tasks"]["by_status"].get("succeeded", 0) == _count(
        app, CeleryTaskRecord, CeleryTaskRecord.created_at >= since, CeleryTaskRecord.status == "succeeded")
    assert overview["review"]["queue_depth"] == _count(app, ReviewSubmission,
                                                       ReviewSubmission.status == "review_pending")
    assert overview["quota_incidents"]["count"] == _count(app, QuotaIncident,
                                                          QuotaIncident.created_at >= since)
    assert overview["quota_incidents"]["by_code"] == {"HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED": 1}
    assert overview["risk"]["permission_denials"] == _count(
        app, ToolExecution, ToolExecution.started_at >= since, ToolExecution.status == "permission_denied")

    # ---- 11. 配额治理视图：用量、趋势、事故史（稳定码）
    quota_view = client.get(f"/api/ops/households/{creator_household}/quota", headers=admin).json()
    month = utcnow().strftime("%Y-%m")
    assert quota_view["usage"]["month"] == month
    assert quota_view["usage"]["requests"] == _count(app, ModelUsage, ModelUsage.household_id == creator_household,
                                                     ModelUsage.month == month)
    assert quota_view["incidents"][0]["code"] == "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"
    incidents = client.get("/api/ops/quota/incidents?household_id=%s" % creator_household,
                           headers=admin).json()
    assert incidents["total"] == 1 and incidents["items"][0]["household_id"] == creator_household  # 租户标签

    # ---- 12. 全决策链审计完整且已脱敏（setup -> grant -> review -> takedown）
    actions = client.get("/api/audit/admin-actions?page_size=100", headers=admin).json()
    corpus = json.dumps([item["detail"] for item in actions["items"]], ensure_ascii=False)
    assert {"models.endpoint.create", "models.profile.create", "models.binding.bind",
            "models.grant.create", "tools.grant.create", "review.approve", "review.takedown",
            "quota.configure"} <= {row["action"] for row in actions["items"]}
    assert SECRET not in corpus and "password" not in corpus and "Traceback" not in corpus

    # ---- 13. 跨家庭隔离：家庭用户 403；管理员视图带租户标识
    demo = _login(client)
    for path in ("/api/audit/agent-runs", "/api/audit/tool-executions", "/api/audit/model-usage",
                 "/api/audit/celery-tasks", "/api/audit/admin-actions", "/api/audit/deployments/pointers",
                 "/api/ops/overview", f"/api/ops/households/{creator_household}/quota",
                 "/api/ops/quota/incidents"):
        assert client.get(path, headers=demo).status_code == 403, path
    assert client.get(f"/api/audit/agent-runs?household_id={buyer_household}", headers=admin).json()["items"][0][
        "household_id"] == buyer_household


def _sum_tokens(app, since):
    from backend.app.db.models import ModelUsage

    with app.state.database.session_factory() as db:
        return db.scalar(select(func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
            ModelUsage.created_at >= since)) or 0
