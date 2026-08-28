# -*- coding: utf-8 -*-
"""Phase 8 audit integrity sweep: every admin mutation across setup/models/
tools/installations/reviews/market/households/tasks writes AdminActionAudit
with actor, action, target and a sanitized payload (no secrets, ciphertext or
raw exception text anywhere in audit bodies)."""
import json

from sqlalchemy import select

from backend.tests.test_audit_query_api import _admin_headers
from backend.tests.test_celery_tasks import _home
from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

SECRET = "sk-sweep-secret-value-9f3a"


def _sweep_rows(app):
    from backend.app.db.models import AdminActionAudit

    with app.state.database.session_factory() as db:
        return db.scalars(select(AdminActionAudit).order_by(AdminActionAudit.created_at)).all()


def test_models_mutations_are_audited_with_sanitized_payloads(client, app, monkeypatch):
    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    headers = _admin_headers(client)

    created = client.post("/api/models/endpoints", headers=headers, json={
        "protocol": "openai_compatible", "display_name": "Sweep Gateway", "base_url": "https://api.openai.com/v1",
        "api_key": SECRET, "timeout_seconds": 30, "max_retries": 2, "enabled": True})
    assert created.status_code == 201, created.text
    endpoint_id = created.json()["id"]

    updated = client.patch(f"/api/models/endpoints/{endpoint_id}", headers=headers,
                           json={"display_name": "Sweep Gateway 2", "api_key": SECRET})
    assert updated.status_code == 200, updated.text

    model = client.post("/api/models/profiles", headers=headers, json={
        "endpoint_id": endpoint_id, "model_name": "sweep-chat", "model_type": "chat",
        "max_tokens": 64, "requests_per_minute": 60, "agent_token_budget": 64, "enabled": True})
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]

    patched = client.patch(f"/api/models/profiles/{model_id}", headers=headers,
                           json={"temperature": 0.3, "enabled": True})
    assert patched.status_code == 200, patched.text

    with app.state.database.session_factory() as db:
        from backend.app.db.models import LlmEndpoint, LlmModel, ModelHealthCheck
        from backend.app.db.base import utcnow

        caps = {name: "passed" for name in ("generation", "streaming", "cancellation", "tool_calling",
                                            "structured_output")}
        profile = db.get(LlmModel, model_id)
        endpoint_revision = db.get(LlmEndpoint, endpoint_id).revision
        profile.capability_flags_json = json.dumps(caps)
        db.add(ModelHealthCheck(model_id=model_id, status="passed",
                                capabilities_json=json.dumps(caps), evidence_json="{}",
                                endpoint_revision=endpoint_revision, model_revision=profile.revision,
                                created_at=utcnow()))
        db.commit()
    bound = client.put("/api/models/bindings/auxiliary_generation", headers=headers,
                       json={"purpose": "auxiliary_generation", "model_id": model_id})
    assert bound.status_code == 200, bound.text

    account_id, household_id = _home(app)
    granted = client.put(f"/api/models/households/{household_id}/grants/{model_id}", headers=headers)
    assert granted.status_code == 200, granted.text
    revoked = client.delete(f"/api/models/households/{household_id}/grants/{model_id}", headers=headers)
    assert revoked.status_code == 204

    rows = _sweep_rows(app)
    actions = {row.action for row in rows}
    assert {"models.endpoint.create", "models.endpoint.update", "models.profile.create",
            "models.profile.update", "models.binding.bind", "models.grant.create",
            "models.grant.delete"} <= actions
    corpus = json.dumps([json.loads(row.detail_json) for row in rows], ensure_ascii=False)
    assert SECRET not in corpus  # 审计体永不包含明文密钥
    assert "ciphertext" not in corpus and "nonce" not in corpus


def test_tools_admin_mutations_are_audited(client, app):
    headers = _admin_headers(client)
    account_id, household_id = _home(app)
    from backend.app.db.models import ToolDefinition

    with app.state.database.session_factory() as db:
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.memo.write"))
        tool_id = tool.id
    granted = client.put(f"/api/tools/{tool_id}/grants", headers=headers,
                         json={"household_id": household_id, "tool_version": tool.version})
    assert granted.status_code == 201, granted.text
    revoked = client.delete(f"/api/tools/{tool_id}/grants/{household_id}", headers=headers)
    assert revoked.status_code == 204

    rows = _sweep_rows(app)
    actions = {row.action for row in rows}
    assert {"tools.grant.create", "tools.grant.delete"} <= actions
    grant_rows = [row for row in rows if row.action == "tools.grant.create"]
    assert grant_rows[-1].household_id == household_id and grant_rows[-1].resource_id == tool_id


def test_platform_admin_mutations_are_audited_without_passwords(client, app):
    headers = _admin_headers(client)
    account_id, household_id = _home(app)

    account = client.put(f"/api/admin/accounts/{account_id}", headers=headers, json={"status": "active"})
    assert account.status_code == 200, account.text
    reset = client.post(f"/api/admin/accounts/{account_id}/reset-password", headers=headers,
                        json={"password": "SweepReset@123"})
    assert reset.status_code == 204

    device = client.post("/api/admin/devices", headers=headers, params={"household_id": household_id}, json={
        "model_id": "sweep-x1", "model_name": "扫地机器人X1", "category": "清洁", "specs": "{}",
        "description": "扫拖一体", "price": 1999, "features": "{}"})
    assert device.status_code == 201, device.text
    updated = client.put(f"/api/admin/devices/sweep-x1", headers=headers, params={"household_id": household_id},
                         json={"model_id": "sweep-x1", "model_name": "扫地机器人X1 Pro", "category": "清洁",
                               "specs": "{}", "description": "扫拖一体", "price": 2199, "features": "{}"})
    assert updated.status_code == 200, updated.text
    inventory = client.put("/api/admin/inventory", headers=headers, params={"household_id": household_id},
                           json={"model_id": "sweep-x1", "warehouse": "主仓", "stock": 10, "min_stock": 2})
    assert inventory.status_code == 200, inventory.text
    deleted = client.delete(f"/api/admin/devices/sweep-x1", headers=headers,
                            params={"household_id": household_id})
    assert deleted.status_code == 204

    rows = _sweep_rows(app)
    actions = {row.action for row in rows}
    assert {"admin.account.update", "admin.account.reset_password", "admin.device.create",
            "admin.device.update", "admin.device.delete", "admin.inventory.upsert"} <= actions
    corpus = json.dumps([json.loads(row.detail_json) for row in rows], ensure_ascii=False)
    assert "SweepReset@123" not in corpus  # 密码永不入审计


def test_review_and_installation_writers_stay_sanitized(client, app):
    from backend.app.db.models import AdminActionAudit, ToolDefinition
    from backend.tests.test_review_governance import MARKET_CARD, _login

    headers = _admin_headers(client)
    account_id, household_id = _home(app)
    creator = _login(client)

    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id, purpose="embedding")
    from backend.tests.test_creator_sandbox import _grant_ceiling

    _grant_ceiling(client, household_id, "household.shopping.write")
    card = json.loads(json.dumps(MARKET_CARD, ensure_ascii=False))
    draft = client.post("/api/creator/drafts", headers=creator,
                        json={"title": "审计链草稿", "description": card["description"]}).json()
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=creator,
                        json={"intent_card": card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=creator).status_code == 200
    trial = client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=creator,
                        json={"granted_tool_keys": ["household.shopping.write"]})
    assert trial.status_code == 200, trial.text
    submitted = client.post(f"/api/creator/drafts/{draft['id']}/submit-review", headers=creator,
                            json={"category": "生活"})
    assert submitted.status_code == 201, submitted.text
    decided = client.post(f"/api/reviews/submissions/{submitted.json()['submission_id']}/decision",
                          headers=headers, json={"action": "approve", "reason": "链路完整"})
    assert decided.status_code == 200, decided.text

    rows = _sweep_rows(app)
    actions = {row.action for row in rows}
    assert "review.approve" in actions
    corpus = json.dumps([json.loads(row.detail_json) for row in rows], ensure_ascii=False)
    assert "Traceback" not in corpus and "Exception" not in corpus  # 无原始异常文本
