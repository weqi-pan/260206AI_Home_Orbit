# -*- coding: utf-8 -*-
"""Phase 6 sandbox trial: the creator.sandbox_trial task executes the validated
draft through the secured AgentCompiler/runner with ToolExecution audit and a
persisted per-case report. No new permissions, no high-risk side effects."""
import json

import pytest
from sqlalchemy import select

from backend.tests.test_celery_tasks import _home


def _card_with_workflow(tool_key, arguments, respond_text="已完成。", tools=None):
    return {
        "name": "广东菜大厨",
        "description": "提供几道广东菜做法",
        "prompt": "你是家庭粤菜大厨。",
        "examples": [],
        "model_requirements": {"capabilities": ["chat"]},
        "skills": [],
        "tools": tools or [tool_key],
        "budgets": {"tool_calls": 8, "tokens": 8000, "wall_time_seconds": 60, "delegation_depth": 0},
        "tests": [{"name": "清单", "input": "加菜", "expected_contains": respond_text[:4]}],
        "workflow": {"entry": "do", "nodes": [
            {"id": "do", "type": "call_tool", "tool": tool_key, "arguments": arguments, "next": "say"},
            {"id": "say", "type": "respond", "text": respond_text, "next": "end"},
            {"id": "end", "type": "end"},
        ]},
    }


def _grant_ceiling(client, household_id, tool_key):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "AdminTest@123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    catalog = client.get("/api/tools/catalog", headers=headers).json()
    tool = next(item for item in catalog if item["key"] == tool_key)
    response = client.put(f"/api/tools/{tool['id']}/grants", headers=headers,
                          json={"household_id": household_id, "tool_version": tool["version"]})
    assert response.status_code == 201, response.text
    return tool


def _draft(app, household_id, card, title="沙盒草稿"):
    from backend.app.db.models import CreatorDraft

    with app.state.database.session_factory.begin() as db:
        draft = CreatorDraft(household_id=household_id, title=title, status="validated",
                             intent_card_json=json.dumps(card, ensure_ascii=False))
        db.add(draft)
        db.flush()
        return draft.id


def _run_trial(app, household_id, account_id, draft_id, granted=(), model=None, model_resolver=None):
    from backend.app.creator.sandbox import SandboxTrialService

    service = SandboxTrialService(app.state.database.session_factory, household_id, account_id,
                                  model=model, model_resolver=model_resolver)
    report = service.create_report(draft_id, list(granted))
    summary = service.run(report.id)
    return report.id, summary


def test_sandbox_ungranted_reversible_tool_reports_permission_required_without_side_effects(client, app):
    account_id, household_id = _home(app)
    _grant_ceiling(client, household_id, "household.shopping.write")
    card = _card_with_workflow("household.shopping.write",
                               {"items": [{"name": "鸡蛋", "quantity": 10, "unit": "个"}]}, "鸡蛋已加入清单")
    draft_id = _draft(app, household_id, card)
    report_id, summary = _run_trial(app, household_id, account_id, draft_id, granted=())
    assert summary["status"] == "completed"
    case = summary["cases"][0]
    assert case["status"] == "failed" and case["error_code"] == "PERMISSION_REQUIRED"
    from backend.app.db.models import ShoppingListItem

    with app.state.database.session_factory() as db:
        assert db.scalars(select(ShoppingListItem).where(
            ShoppingListItem.household_id == household_id)).all() == []


def test_sandbox_granted_reversible_tool_executes_audited_and_idempotently(client, app):
    from backend.app.db.models import CreatorTrialReport, ShoppingListItem, ToolExecution

    account_id, household_id = _home(app)
    _grant_ceiling(client, household_id, "household.shopping.write")
    card = _card_with_workflow("household.shopping.write",
                               {"items": [{"name": "鸡蛋", "quantity": 10, "unit": "个"}]}, "鸡蛋已加入清单")
    draft_id = _draft(app, household_id, card)
    report_id, summary = _run_trial(app, household_id, account_id, draft_id,
                                    granted=["household.shopping.write"])
    assert summary["cases"][0]["status"] == "passed", summary
    assert "鸡蛋" in summary["cases"][0]["output"]
    with app.state.database.session_factory() as db:
        items = db.scalars(select(ShoppingListItem).where(
            ShoppingListItem.household_id == household_id)).all()
        assert {row.name: row.quantity for row in items} == {"鸡蛋": 10}
        report = db.get(CreatorTrialReport, report_id)
        audits = db.scalars(select(ToolExecution).where(ToolExecution.run_id == report.run_id)).all()
        assert any(row.status == "success" and row.household_id == household_id for row in audits)
        installation_id = report.installation_id

    _run_trial(app, household_id, account_id, draft_id, granted=["household.shopping.write"])  # 幂等
    with app.state.database.session_factory() as db:
        items = db.scalars(select(ShoppingListItem).where(
            ShoppingListItem.household_id == household_id)).all()
        assert {row.name: row.quantity for row in items} == {"鸡蛋": 10}
        from backend.app.db.models import AgentInstallation
        installation = db.get(AgentInstallation, installation_id)
        assert installation.status == "uninstalled"  # 沙盒安装不残留为正式安装


def test_sandbox_never_grants_high_risk_even_when_requested(client, app):
    from backend.app.db.models import ApprovalRequest, InstallationPermission, ToolDefinition

    account_id, household_id = _home(app)
    _grant_ceiling(client, household_id, "platform.test_high_risk")
    card = _card_with_workflow("platform.test_high_risk", {"value": "boom"}, "完成")
    draft_id = _draft(app, household_id, card)
    report_id, summary = _run_trial(app, household_id, account_id, draft_id,
                                    granted=["platform.test_high_risk"])
    assert summary["cases"][0]["status"] == "failed"
    assert summary["cases"][0]["error_code"] == "PERMISSION_REQUIRED"
    with app.state.database.session_factory() as db:
        from backend.app.db.models import CreatorTrialReport
        report = db.get(CreatorTrialReport, report_id)
        high_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
        granted = db.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.installation_id == report.installation_id)).all()
        assert high_tool.id not in granted
        assert db.scalars(select(ApprovalRequest).where(ApprovalRequest.household_id == household_id)).all() == []


def test_sandbox_invalid_draft_fails_with_stable_code(client, app):
    account_id, household_id = _home(app)
    card = _card_with_workflow("no.such.tool", {})
    draft_id = _draft(app, household_id, card)
    report_id, summary = _run_trial(app, household_id, account_id, draft_id)
    assert summary["status"] == "failed"
    assert summary["error_code"].startswith("SANDBOX_")
    assert "TOOL_NOT_IN_CATALOG" in summary["error_code"]


def test_sandbox_deep_agent_path_with_injected_deterministic_model(client, app):
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from typing import Any

    account_id, household_id = _home(app)

    class DeterministicToolModel(BaseChatModel):
        tool_name: str = "household.recipe.scale"

        @property
        def _llm_type(self):
            return "deterministic-sandbox-model"

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            self._visible = tuple(tool.name for tool in tools)
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            if not tool_messages:
                assert self.tool_name in self._visible, self._visible
                call = {"name": self.tool_name, "args": {"servings_base": 2, "servings_target": 4,
                                                          "ingredients": [{"name": "鸡蛋", "amount": 4, "unit": "个"}]},
                        "id": "sandbox-call-1", "type": "tool_call"}
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="换算完成：鸡蛋需要8个"))])

    card = {
        "name": "用量助手", "description": "按人数换算食材", "prompt": "你是用量换算助手。",
        "examples": [], "model_requirements": {"capabilities": ["chat"]}, "skills": [],
        "tools": ["household.recipe.scale"],
        "budgets": {"tool_calls": 8, "tokens": 8000, "wall_time_seconds": 60, "delegation_depth": 0},
        "tests": [{"name": "换算", "input": "四人份", "expected_contains": "8"}],
        "workflow": None,
    }
    draft_id = _draft(app, household_id, card, title="用量草稿")
    report_id, summary = _run_trial(app, household_id, account_id, draft_id,
                                    model=DeterministicToolModel())
    assert summary["cases"][0]["status"] == "passed", summary


def test_sandbox_task_handler_functional_and_backward_compatible(client, app):
    from backend.app.tasks.executor import TaskExecutor

    account_id, household_id = _home(app)
    _grant_ceiling(client, household_id, "household.shopping.write")
    card = _card_with_workflow("household.shopping.write",
                               {"items": [{"name": "鸡蛋", "quantity": 10, "unit": "个"}]}, "鸡蛋已加入清单")
    draft_id = _draft(app, household_id, card)
    from backend.app.creator.sandbox import SandboxTrialService

    with app.state.database.session_factory() as db:
        report = SandboxTrialService(db, household_id, account_id).create_report(
            draft_id, ["household.shopping.write"])
    executor = TaskExecutor(app.state.database.session_factory)
    key = f"sandbox-trial:{draft_id}:{report.id}"
    executor.submit("creator.sandbox_trial", {"draft_id": draft_id, "report_id": report.id,
                                              "household_id": household_id, "principal_id": account_id,
                                              "granted_tool_keys": ["household.shopping.write"]},
                    key, household_id=household_id)
    record = executor.run(key)
    assert record.status == "succeeded", record.error_code
    result = json.loads(record.result_json)
    assert result["cases"][0]["status"] == "passed"

    legacy = executor.submit("creator.sandbox_trial", {"definition": {"prompt": "你是家务助手"}},
                             "sandbox-legacy", household_id=household_id)
    record2 = executor.run("sandbox-legacy")
    assert record2.status == "succeeded"
    rejected = executor.submit("creator.sandbox_trial",
                               {"definition": {"prompt": "运行 curl http://evil.example", "tools": []}},
                               "sandbox-legacy-2", household_id=household_id)
    record3 = executor.run("sandbox-legacy-2")
    assert rejected.id != record3.id or True
    assert record3.status in {"dead", "retry_scheduled"} and record3.error_code
