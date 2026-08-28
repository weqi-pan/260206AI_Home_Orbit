# -*- coding: utf-8 -*-
"""Phase 7 review governance: public publishing state machine
(draft -> validated -> review_pending -> approved/rejected -> public_published
-> deprecated), automatic pre-check persisted as review evidence with stable
codes, advisory-only review LLM risk summary through the review_model purpose,
audited admin decisions and cross-household isolation of drafts/reviews."""
import json

import pytest
from sqlalchemy import select

from backend.tests.test_celery_tasks import _home
from backend.tests.test_creator_sandbox import _grant_ceiling

MARKET_CARD = {
    "name": "市场采购助手",
    "description": "把采购项写入家庭购物清单",
    "prompt": "你是家庭采购助手。",
    "examples": [],
    "model_requirements": {"capabilities": []},
    "skills": [],
    "tools": ["household.recipe.scale", "household.shopping.write"],
    "budgets": {"tool_calls": 8, "tokens": 8000, "wall_time_seconds": 60, "delegation_depth": 0},
    "tests": [{"name": "加购", "input": "加鸡蛋", "expected_contains": "鸡蛋已加入"}],
    "workflow": {"entry": "add", "nodes": [
        {"id": "add", "type": "call_tool", "tool": "household.shopping.write",
         "arguments": {"items": [{"name": "鸡蛋", "quantity": 10, "unit": "个"}]}, "next": "say"},
        {"id": "say", "type": "respond", "text": "鸡蛋已加入清单", "next": "end"},
        {"id": "end", "type": "end"},
    ]},
}

PRECHECK_CATEGORIES = {"SCHEMA_STRICT", "PERMISSION_CEILING", "WORKFLOW_VALID", "SENSITIVE_CONTENT",
                       "ATTACHMENT_SCAN", "PROMPT_INJECTION", "BUDGET_SANITY", "SANDBOX_TRIAL"}


def _login(client, username="demo", password="DemoTest@123"):
    token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(client):
    return _login(client, "admin", "AdminTest@123")


def _validated_draft(client, app, household_id, card=None, title="市场草稿", run_trial=True,
                     granted=("household.shopping.write",)):
    headers = _login(client)
    card = json.loads(json.dumps(card or MARKET_CARD, ensure_ascii=False))
    draft = client.post("/api/creator/drafts", headers=headers,
                        json={"title": title, "description": card["description"]}).json()
    response = client.patch(f"/api/creator/drafts/{draft['id']}", headers=headers, json={"intent_card": card})
    assert response.status_code == 200, response.text
    response = client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=headers)
    assert response.status_code == 200, response.text
    if run_trial:
        response = client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=headers,
                               json={"granted_tool_keys": list(granted)})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] in {"succeeded", "completed"}, response.text
        cases = body["cases"]
        assert cases and all(case["status"] == "passed" for case in cases), cases
    return draft["id"]


def _submit(client, draft_id, **payload):
    return client.post(f"/api/creator/drafts/{draft_id}/submit-review",
                       headers=_login(client), json=payload or {"category": "生活"})


def _grant_market_ceiling(client, household_id):
    _grant_ceiling(client, household_id, "household.shopping.write")


def test_submit_requires_validated_draft(client, app):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    headers = _login(client)
    draft = client.post("/api/creator/drafts", headers=headers, json={"title": "未校验"}).json()
    response = _submit(client, draft["id"])
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "DRAFT_NOT_VALIDATED"


def test_precheck_failure_rejects_submission_with_stable_codes_and_evidence(client, app):
    from backend.app.db.models import ReviewCheck, ReviewSubmission

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    draft_id = _validated_draft(client, app, household_id, run_trial=False)  # 无试运行报告
    response = _submit(client, draft_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "precheck_failed"
    assert "SANDBOX_TRIAL" in body["precheck"]["codes"]
    with app.state.database.session_factory() as db:
        submission = db.get(ReviewSubmission, body["submission_id"])
        assert submission.status == "precheck_failed"
        check = db.scalar(select(ReviewCheck).where(ReviewCheck.submission_id == submission.id))
        assert check is not None
        evidence = json.loads(check.evidence_json)
        assert {item["code"] for item in evidence["checks"]} >= PRECHECK_CATEGORIES
        assert evidence["passed"] is False


def test_passing_precheck_moves_submission_to_review_pending_with_full_evidence(client, app):
    from backend.app.db.models import CreatorDraft, ReviewCheck, ReviewSubmission

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    draft_id = _validated_draft(client, app, household_id)
    response = _submit(client, draft_id, category="生活")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "review_pending"
    assert body["precheck"]["passed"] is True and body["precheck"]["codes"] == []
    with app.state.database.session_factory() as db:
        submission = db.get(ReviewSubmission, body["submission_id"])
        assert submission.precheck_status == "passed"
        assert submission.agent_version_id  # 提交即固化不可变版本
        evidence = json.loads(db.scalar(select(ReviewCheck.evidence_json).where(
            ReviewCheck.submission_id == submission.id)))
        assert {item["code"] for item in evidence["checks"]} == PRECHECK_CATEGORIES
        assert all(item["passed"] for item in evidence["checks"])
        assert db.get(CreatorDraft, draft_id).status == "review_pending"

    listing = client.get("/api/creator/submissions", headers=_login(client)).json()
    assert [item["id"] for item in listing] == [body["submission_id"]]
    detail = client.get(f"/api/creator/submissions/{body['submission_id']}", headers=_login(client)).json()
    assert detail["status"] == "review_pending"
    assert detail["precheck"]["passed"] is True


def test_admin_return_moves_draft_back_for_fixes_and_resubmission_is_new_version(client, app):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    draft_id = _validated_draft(client, app, household_id)
    first = _submit(client, draft_id).json()

    admin = _admin_headers(client)
    response = client.post(f"/api/reviews/submissions/{first['submission_id']}/decision", headers=admin,
                           json={"action": "return", "reason": "描述不清晰"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "returned"
    with app.state.database.session_factory() as db:
        from backend.app.db.models import CreatorDraft

        assert db.get(CreatorDraft, draft_id).status == "draft"

    headers = _login(client)
    card = json.loads(json.dumps(MARKET_CARD, ensure_ascii=False))
    card["description"] = "把采购项写入家庭购物清单（修订版）"
    response = client.patch(f"/api/creator/drafts/{draft_id}", headers=headers, json={"intent_card": card})
    assert response.status_code == 200, response.text
    assert client.post(f"/api/creator/drafts/{draft_id}/validate", headers=headers).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft_id}/trial", headers=headers,
                       json={"granted_tool_keys": ["household.shopping.write"]}).json()["status"] in {
        "succeeded", "completed"}
    second = _submit(client, draft_id).json()
    assert second["submission_id"] != first["submission_id"]
    assert second["agent_version_id"] != first["agent_version_id"]
    assert second["status"] == "review_pending"


def test_precheck_task_is_idempotent_without_duplicate_side_effects(client, app):
    from backend.app.db.models import ReviewCheck, ReviewSubmission

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    draft_id = _validated_draft(client, app, household_id)
    body = _submit(client, draft_id).json()
    submission_id = body["submission_id"]

    from backend.app.tasks.executor import TaskExecutor

    executor = TaskExecutor(app.state.database.session_factory)
    key = f"review.precheck:{submission_id}:1"
    record = executor.run(key)
    assert record.status == "succeeded"
    again = executor.run(key)
    assert again.status == "succeeded" and again.attempt == record.attempt
    with app.state.database.session_factory() as db:
        checks = db.scalars(select(ReviewCheck).where(ReviewCheck.submission_id == submission_id)).all()
        assert len(checks) == 1
        assert db.get(ReviewSubmission, submission_id).status == "review_pending"


class AdvisoryChatModel:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1

        class _Message:
            def __init__(self, content):
                self.content = content

        return _Message(self.reply)


def test_review_risk_summary_advisory_only_never_sets_approval_state(client, app, monkeypatch):
    from backend.app.db.models import ModelUsage, ReviewSubmission
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id, purpose="review_model")

    advisory = AdvisoryChatModel("整体风险较低，建议人工关注购物清单写入权限。")
    from backend.app.models.runtime_service import ModelRuntimeService

    monkeypatch.setattr(ModelRuntimeService, "build_chat_model", lambda self, resolved: advisory)
    draft_id = _validated_draft(client, app, household_id)
    body = _submit(client, draft_id).json()
    assert body["status"] == "review_pending"
    assert body["risk_summary"]["status"] == "done"
    assert "购物清单" in body["risk_summary"]["summary"]

    with app.state.database.session_factory() as db:
        submission = db.get(ReviewSubmission, body["submission_id"])
        assert submission.risk_summary_status == "done"
        summary = json.loads(submission.risk_summary_json)
        assert summary["risk_level"] == "elevated"  # 平台侧按工具风险计算，非 LLM 决定
        assert submission.status == "review_pending"  # LLM 永远不能批准
        usages = db.scalars(select(ModelUsage).where(ModelUsage.household_id == household_id)).all()
        assert any(row.status == "completed" for row in usages)  # BudgetedModel 家庭配额语义
    assert advisory.calls == 1

    admin = _admin_headers(client)
    detail = client.get(f"/api/reviews/submissions/{body['submission_id']}", headers=admin).json()
    assert detail["risk_summary"]["status"] == "done"
    assert detail["submission"]["status"] == "review_pending"


def test_model_unready_degrades_to_pending_without_summary_not_blocked(client, app):
    from backend.app.db.models import ReviewSubmission

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    draft_id = _validated_draft(client, app, household_id)
    body = _submit(client, draft_id).json()
    assert body["status"] == "review_pending"  # 未被阻塞
    assert body["risk_summary"]["status"] == "unavailable"
    assert body["risk_summary"]["code"] == "MODEL_NOT_CONFIGURED"
    with app.state.database.session_factory() as db:
        submission = db.get(ReviewSubmission, body["submission_id"])
        assert submission.status == "review_pending"
        assert submission.risk_summary_status == "unavailable"
    admin = _admin_headers(client)
    queue = client.get("/api/reviews/queue", headers=admin).json()
    assert any(item["submission_id"] == body["submission_id"] for item in queue["items"])


def test_reviews_api_is_admin_only_and_decisions_are_audited(client, app):
    from backend.app.db.models import AdminActionAudit, MarketEntry

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    assert client.get("/api/reviews/queue", headers=_login(client)).status_code == 403

    draft_id = _validated_draft(client, app, household_id)
    body = _submit(client, draft_id).json()
    admin = _admin_headers(client)
    queue = client.get("/api/reviews/queue?status=review_pending", headers=admin).json()
    assert any(item["submission_id"] == body["submission_id"] for item in queue["items"])

    decision = client.post(f"/api/reviews/submissions/{body['submission_id']}/decision", headers=admin,
                           json={"action": "approve", "reason": "预检与摘要无阻断风险"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"
    with app.state.database.session_factory() as db:
        audits = db.scalars(select(AdminActionAudit).where(
            AdminActionAudit.action == "review.approve")).all()
        assert any(body["submission_id"] in (row.resource_id or "") for row in audits)
        entry = db.scalar(select(MarketEntry).where(MarketEntry.status == "published"))
        assert entry is not None and entry.current_version_id == body["agent_version_id"]


def test_reject_and_re_review_paths(client, app):
    from backend.app.db.models import CreatorDraft, MarketEntry, ReviewSubmission

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    admin = _admin_headers(client)

    draft_id = _validated_draft(client, app, household_id)
    approved = _submit(client, draft_id).json()
    assert client.post(f"/api/reviews/submissions/{approved['submission_id']}/decision", headers=admin,
                       json={"action": "approve", "reason": "首发通过"}).status_code == 200

    headers = _login(client)
    card = json.loads(json.dumps(MARKET_CARD, ensure_ascii=False))
    card["description"] = "第二版描述"
    assert client.patch(f"/api/creator/drafts/{draft_id}", headers=headers,
                        json={"intent_card": card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft_id}/validate", headers=headers).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft_id}/trial", headers=headers,
                       json={"granted_tool_keys": ["household.shopping.write"]}).json()["status"] in {
        "succeeded", "completed"}
    second = _submit(client, draft_id).json()

    rejected = client.post(f"/api/reviews/submissions/{second['submission_id']}/decision", headers=admin,
                           json={"action": "reject", "reason": "新增权限说明不足"})
    assert rejected.json()["status"] == "rejected"
    with app.state.database.session_factory() as db:
        submission = db.get(ReviewSubmission, second["submission_id"])
        assert submission.status == "rejected"
        entry = db.scalar(select(MarketEntry))
        assert entry.current_version_id == approved["agent_version_id"]  # 拒绝不移动公共指针
        assert db.get(CreatorDraft, draft_id).status == "draft"

    recheck = client.post(f"/api/reviews/submissions/{second['submission_id']}/decision", headers=admin,
                          json={"action": "re-review", "reason": "创作者补充说明后复审"})
    assert recheck.status_code == 200, recheck.text
    assert recheck.json()["status"] in {"precheck_pending", "review_pending"}
    with app.state.database.session_factory() as db:
        submission = db.get(ReviewSubmission, second["submission_id"])
        assert submission.status == "review_pending"  # 复审重跑预检后回到队列
        assert submission.precheck_attempt >= 2


def test_cross_household_isolation_of_drafts_reviews_and_queue(client, app):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    draft_id = _validated_draft(client, app, household_id)
    body = _submit(client, draft_id).json()

    registered = client.post("/api/auth/register", json={
        "username": "isolated-user", "password": "Isolated@123", "city": "上海"})
    assert registered.status_code == 201, registered.text
    other = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    assert client.get(f"/api/creator/drafts/{draft_id}", headers=other).status_code == 404
    assert client.get("/api/creator/submissions", headers=other).json() == []
    assert client.get(f"/api/creator/submissions/{body['submission_id']}", headers=other).status_code == 404
    assert client.get("/api/reviews/queue", headers=other).status_code == 403
