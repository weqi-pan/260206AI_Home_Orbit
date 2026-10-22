# -*- coding: utf-8 -*-
"""Phase 8 quota governance: household quota view/adjust APIs (admin, audited,
validated), usage trends, over-limit incident history with the stable code that
fired, expired-reservation release, and adjustments affecting new reservations
only."""
import json
from datetime import timedelta

from sqlalchemy import select

from backend.tests.test_audit_query_api import _admin_headers, _user_headers
from backend.tests.test_celery_tasks import _home
from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime


def test_household_quota_view_reconciles_usage_trend_and_incidents(client, app, monkeypatch):
    from backend.app.db.base import utcnow
    from backend.app.db.models import ModelUsage, QuotaIncident

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id)
        model_id = model.id
        month = utcnow().strftime("%Y-%m")
        db.add(ModelUsage(household_id=household_id, model_id=model_id, month=month,
                          tokens=20, reserved_tokens=20, status="completed"))
        db.commit()
        db.add(QuotaIncident(household_id=household_id, model_id=model_id, code="MODEL_RATE_LIMIT_EXCEEDED"))
        db.commit()

    view = client.get(f"/api/ops/households/{household_id}/quota", headers=headers)
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["household_id"] == household_id
    assert body["quota"]["monthly_limit"] == 100 and body["quota"]["requests_per_minute"] == 10
    assert body["quota"]["agent_token_budget"] == 8
    assert body["usage"]["month"] == month
    assert body["usage"]["tokens"] == 20 and body["usage"]["requests"] == 1
    assert body["trend"] and body["trend"][-1]["tokens"] == 20
    assert body["incidents"][0]["code"] == "MODEL_RATE_LIMIT_EXCEEDED"

    missing = client.get("/api/ops/households/no-such-household/quota", headers=headers)
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "HOUSEHOLD_NOT_FOUND"
    denied = client.get(f"/api/ops/households/{household_id}/quota", headers=_user_headers(client))
    assert denied.status_code == 403


def test_quota_adjustment_is_audited_validated_and_applies_to_new_reservations_only(client, app, monkeypatch):
    from backend.app.db.models import AdminActionAudit, ModelUsage, UsageQuota
    from backend.app.llm_runtime.quotas import QuotaService

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id)
        model_id = model.id

    missing = client.put("/api/models/households/no-such-household/quota", headers=headers,
                         json={"monthly_limit": 50, "requests_per_minute": 5, "agent_token_budget": 8})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "HOUSEHOLD_NOT_FOUND"

    adjusted = client.put(f"/api/models/households/{household_id}/quota", headers=headers,
                          json={"monthly_limit": 60, "requests_per_minute": 6, "agent_token_budget": 8})
    assert adjusted.status_code == 200 and adjusted.json()["monthly_limit"] == 60
    with app.state.database.session_factory() as db:
        audits = db.scalars(select(AdminActionAudit).where(
            AdminActionAudit.action == "quota.configure",
            AdminActionAudit.resource_id == household_id)).all()
        assert audits and json.loads(audits[-1].detail_json)["monthly_limit"] == 60
        quota = db.scalar(select(UsageQuota).where(UsageQuota.household_id == household_id))
        assert quota.monthly_limit == 60

        # 新预留按新配额执行；已完成的历史行不被回溯改动
        usage = QuotaService(db).reserve(household_id, model_id, 8)
        QuotaService(db).complete(usage.id)
        assert QuotaService(db).current_usage(household_id) == 8


def test_quota_incident_listing_paginated_and_filtered(client, app):
    from backend.app.db.models import QuotaIncident

    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    with app.state.database.session_factory.begin() as db:
        for index in range(12):
            db.add(QuotaIncident(household_id=household_id, model_id="m",
                                 code="HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED" if index % 2 else "MODEL_RATE_LIMIT_EXCEEDED"))
    page1 = client.get("/api/ops/quota/incidents?page=1&page_size=10", headers=headers).json()
    assert page1["total"] == 12 and len(page1["items"]) == 10
    page2 = client.get("/api/ops/quota/incidents?page=2&page_size=10", headers=headers).json()
    assert len(page2["items"]) == 2
    by_code = client.get("/api/ops/quota/incidents?code=MODEL_RATE_LIMIT_EXCEEDED", headers=headers).json()
    assert by_code["total"] == 6
    by_household = client.get(f"/api/ops/quota/incidents?household_id={household_id}", headers=headers).json()
    assert by_household["total"] == 12
    assert client.get("/api/ops/quota/incidents", headers=_user_headers(client)).status_code == 403


def test_release_expired_reconciles_stale_reservations_conservatively(client, app, monkeypatch):
    from backend.app.db.base import utcnow
    from backend.app.db.models import AdminActionAudit, ModelUsage
    from backend.app.llm_runtime.quotas import QuotaService

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    headers = _admin_headers(client)
    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id)
        model_id = model.id
        stale = ModelUsage(household_id=household_id, model_id=model_id,
                           month=utcnow().strftime("%Y-%m"), tokens=8, reserved_tokens=8, status="reserved")
        stale.created_at = utcnow() - timedelta(hours=3)
        db.add(stale)
        db.commit()
        stale_id = stale.id
        before_usage = QuotaService(db).current_usage(household_id)

    released = client.post("/api/ops/model-usage/release-expired", headers=headers)
    assert released.status_code == 200, released.text
    assert released.json()["released"] >= 1
    with app.state.database.session_factory() as db:
        row = db.get(ModelUsage, stale_id)
        assert row.status == "released"  # 保守计费：tokens 仍计入
        assert QuotaService(db).current_usage(household_id) >= before_usage
        audits = db.scalars(select(AdminActionAudit).where(
            AdminActionAudit.action == "quota.release_expired")).all()
        assert audits and audits[-1].principal_id
    assert client.post("/api/ops/model-usage/release-expired",
                       headers=_user_headers(client)).status_code == 403
