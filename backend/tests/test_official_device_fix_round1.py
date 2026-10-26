import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, select


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password}).json()


def register(client, username):
    return client.post("/api/auth/register", json={
        "username": username, "password": "Password@123", "city": "杭州",
    }).json()


def test_admin_ticket_flow_uses_explicit_tenant_scope_with_two_admins(client, app, settings):
    from backend.app.core.security import hash_password
    from backend.app.db.models import Account, Household, HouseholdMember, Ticket

    demo = login(client, "demo", settings.demo_initial_password)
    ticket = client.post("/api/tickets", headers=bearer(demo["access_token"]),
                         json={"title": "租户工单", "content": "无法启动"}).json()
    admin = login(client, "admin", settings.admin_initial_password)
    with app.state.database.session_factory.begin() as db:
        second = Account(username="second-admin", password_hash=hash_password("Password@123", rounds=4), role="admin")
        db.add(second); db.flush()
        home = Household(name="Second Admin Home", created_by_account_id=second.id)
        db.add(home); db.flush()
        db.add(HouseholdMember(household_id=home.id, account_id=second.id, role="owner"))
        ticket_household = db.get(Ticket, ticket["id"]).household_id
        second_id = second.id
        second_home_id = home.id
    second = login(client, "second-admin", "Password@123")

    listed = client.get("/api/admin/tickets", headers=bearer(second["access_token"])).json()
    assert any(row["id"] == ticket["id"] and row["household_id"] == ticket_household for row in listed)
    updated = client.put(f"/api/admin/households/{ticket_household}/tickets/{ticket['id']}",
                         headers=bearer(second["access_token"]),
                         json={"status": "resolved", "admin_reply": "已处理"})
    assert (updated.status_code, updated.json()["status"]) == (200, "resolved")
    assert client.put(f"/api/admin/households/{second_home_id}/tickets/{ticket['id']}",
                      headers=bearer(admin["access_token"]), json={"status": "closed"}).status_code == 404
    with app.state.database.session_factory() as db:
        audit = db.scalar(select(__import__("backend.app.db.models", fromlist=["AdminActionAudit"]).AdminActionAudit).where(
            __import__("backend.app.db.models", fromlist=["AdminActionAudit"]).AdminActionAudit.principal_id == second_id,
            __import__("backend.app.db.models", fromlist=["AdminActionAudit"]).AdminActionAudit.resource_id == str(ticket["id"])))
        assert audit and audit.household_id == ticket_household


def test_same_model_inventory_never_crosses_households_through_apis_or_repository(client, app, settings):
    from backend.agent.tools.gateways import ToolRepositories
    from backend.app.db.models import Account, Device, HouseholdMember, Inventory

    other = register(client, "inventory-other")
    with app.state.database.session_factory.begin() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        other_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == other["account"]["id"]))
        other_device = db.scalar(select(Device).where(Device.household_id == other_home, Device.model_id == "R1-Pro"))
        db.scalar(select(Inventory).where(Inventory.household_id == other_home,
                                          Inventory.model_id == "R1-Pro")).stock = 9999
    demo_token = login(client, "demo", settings.demo_initial_password)["access_token"]
    assert client.get("/api/devices/R1-Pro", headers=bearer(demo_token)).json()["total_stock"] == 62
    compared = client.post("/api/devices/compare", headers=bearer(demo_token),
                           json={"model_ids": ["R1-Lite", "R1-Pro"]}).json()
    assert next(row for row in compared if row["model_id"] == "R1-Pro")["total_stock"] == 62
    rows = ToolRepositories(app.state.database.session_factory).inventory(demo_home, "R1-Pro")
    assert sum(row.stock for row in rows) == 62 and all(row.household_id == demo_home for row in rows)


def test_scoped_vector_rebuild_preserves_other_household_and_requires_skill_metadata(tmp_path):
    from backend.rag.vector_store import VectorStoreService

    class Collection:
        def __init__(self): self.deletes = []
        def delete(self, **kwargs): self.deletes.append(kwargs)

    class Store:
        def __init__(self): self._collection = Collection(); self.documents = []
        def add_documents(self, documents, ids): self.documents.extend(documents)

    path = tmp_path / "guide.txt"; path.write_text("保修向量资料", encoding="utf-8")
    store = Store(); service = VectorStoreService(vector_store=store)
    service.rebuild(files=[("file-a", path, "guide.txt")], faqs=[],
                    household_id="home-a", skill_version_id="skill-v1")
    assert store._collection.deletes[0] == {"where": {"$and": [
        {"household_id": "home-a"}, {"skill_version_id": "skill-v1"},
        {"source_type": {"$in": ["kb", "faq"]}},
    ]}}
    assert store.documents[0].metadata["household_id"] == "home-a"
    assert store.documents[0].metadata["skill_version_id"] == "skill-v1"


def test_new_session_defaults_to_official_deployment_and_chat_uses_production_runner(client, app, settings):
    demo = login(client, "demo", settings.demo_initial_password)
    session = client.post("/api/chat/sessions", headers=bearer(demo["access_token"]), json={}).json()
    assert session["deployment_id"]
    calls = []

    class Runner:
        def bind_model(self, run_id, model): pass
        def stream(self, messages, context):
            calls.append(context)
            yield {"event": "token", "data": {"content": "official"}}
            yield {"event": "run", "data": {"run_id": context.run_id, "status": "completed"}}

    runner = Runner()
    app.state.deployment_agent_runner = runner
    app.state.agent_runner = runner
    app.state.enforce_model_runtime_for_injected_runner = False
    response = client.post(f"/api/chat/sessions/{session['id']}/chat", headers=bearer(demo["access_token"]),
                           json={"message": "比较设备"})
    assert response.status_code == 200 and calls and calls[0].deployment_id == session["deployment_id"]


def test_seed_grants_only_low_risk_and_owner_can_authorize_reversible_tool(client, app, settings):
    from backend.app.db.models import Account, AgentInstallation, HouseholdMember, InstallationPermission, ToolDefinition

    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        installation = db.scalar(select(AgentInstallation).where(AgentInstallation.household_id == home,
                                                                  AgentInstallation.authority_slot == "active"))
        granted = set(db.scalars(select(ToolDefinition.key).join(InstallationPermission,
            InstallationPermission.tool_id == ToolDefinition.id).where(
                InstallationPermission.installation_id == installation.id)).all())
        restock = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "device.restock.register"))
    assert "device.restock.register" not in granted and "device.ticket.create" not in granted
    demo_token = login(client, "demo", settings.demo_initial_password)["access_token"]
    response = client.put(f"/api/installations/{installation.id}/permissions", headers=bearer(demo_token),
                          json={"tool_id": restock.id, "tool_version": restock.version})
    assert response.status_code == 201


def test_catalog_sync_rejects_exact_metadata_conflict(client, app):
    from backend.app.api.tools import sync_builtin_catalog
    from backend.app.db.models import ToolDefinition

    with app.state.database.session_factory.begin() as db:
        row = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "device.search"))
        row.description = "tampered"
    with app.state.database.session_factory() as db, pytest.raises(RuntimeError, match="TOOL_CATALOG_MISMATCH"):
        sync_builtin_catalog(db)


def test_membership_composite_constraints_reject_foreign_account_ticket_and_restock(client, app):
    from sqlalchemy.exc import IntegrityError
    from backend.app.db.models import Account, Device, HouseholdMember, RestockNotification, Ticket

    other = register(client, "foreign-owner")
    with app.state.database.session_factory() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        device = db.scalar(select(Device).where(Device.household_id == demo_home, Device.model_id == "R1-Pro"))
        db.add(Ticket(household_id=demo_home, account_id=other["account"]["id"], title="invalid", summary="invalid"))
        with pytest.raises(IntegrityError): db.flush()
        db.rollback()
        db.add(RestockNotification(household_id=demo_home, account_id=other["account"]["id"],
                                   device_id=device.id, model_id=device.model_id))
        with pytest.raises(IntegrityError): db.flush()


def test_fix_round1_migration_drops_global_md5_unique_and_fails_ambiguous_legacy(tmp_path):
    database_path = tmp_path / "round1.db"
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    root = Path(__file__).parents[2]
    assert subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                          cwd=root, env=environment, capture_output=True, text=True).returncode == 0
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{database_path}")
    uniques = inspect(engine).get_unique_constraints("knowledge_files")
    assert {tuple(item["column_names"]) for item in uniques} == {("household_id", "file_md5"), ("id", "household_id")}


def test_official_version_upgrade_pins_installation_and_requires_new_permission(client, app, settings):
    from backend.agent.platform.installations import InstallationService
    from backend.app.db.base import utcnow
    from backend.app.db.seed import seed_official_device_agent
    from backend.app.db.models import (Account, AgentDeployment, AgentInstallation, AgentToolBinding, AgentVersion,
                                       HouseholdMember, InstallationPermission, ToolDefinition)

    with app.state.database.session_factory.begin() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        deployment = db.scalar(select(AgentDeployment).where(AgentDeployment.household_id == home,
                                                               AgentDeployment.visibility == "official"))
        v1_id = deployment.agent_version_id
        installation = db.scalar(select(AgentInstallation).where(AgentInstallation.deployment_id == deployment.id,
                                                                  AgentInstallation.authority_slot == "active"))
        v1_definition = db.get(AgentVersion, v1_id).definition_json
        new_tool = ToolDefinition(key="device.new.read", display_name="新读取", description="新版本读取工具",
                                  risk_level="low", version="1", enabled=True)
        db.add(new_tool); db.flush()
        v2 = AgentVersion(household_id=home, agent_id=db.get(AgentVersion, v1_id).agent_id, version=2,
                          definition_json=v1_definition, published_at=utcnow())
        db.add(v2); db.flush()
        db.add(AgentToolBinding(household_id=home, agent_version_id=v2.id, tool_id=new_tool.id))
        deployment.agent_version_id = v2.id; deployment.revision += 1
        v2_id, deployment_id, installation_id, tool_id = v2.id, deployment.id, installation.id, new_tool.id
    with app.state.database.session_factory() as db:
        installation = db.get(AgentInstallation, installation_id)
        assert installation.agent_version_id == v1_id
        assert db.scalar(select(InstallationPermission).where(
            InstallationPermission.installation_id == installation_id,
            InstallationPermission.tool_id == tool_id)) is None
        upgraded = InstallationService(db, home, demo.id).upgrade(deployment_id, v2_id)
        assert upgraded.agent_version_id == v2_id
        assert db.scalar(select(InstallationPermission).where(
            InstallationPermission.installation_id == upgraded.id, InstallationPermission.tool_id == tool_id)) is None
        deployment = db.get(AgentDeployment, deployment_id)
        deployment.agent_version_id = v1_id; deployment.revision += 1; db.commit()
        assert db.get(AgentVersion, v1_id).definition_json == v1_definition


def test_cross_household_writes_and_deleted_knowledge_do_not_leak(client, app, settings):
    from backend.agent.tools.gateways import ToolRepositories
    from backend.app.db.models import Account, FaqPair, HouseholdMember, KnowledgeFile, RestockNotification, Ticket

    other = register(client, "cross-household")
    demo = login(client, "demo", settings.demo_initial_password)
    demo_ticket = client.post("/api/tickets", headers=bearer(demo["access_token"]),
                              json={"title": "demo-only", "content": "secret"}).json()
    with app.state.database.session_factory.begin() as db:
        demo_account = db.scalar(select(Account).where(Account.username == "demo"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo_account.id))
        other_home = db.scalar(select(HouseholdMember.household_id).where(
            HouseholdMember.account_id == other["account"]["id"]))
        deleted = KnowledgeFile(household_id=demo_home, filename="deleted.txt",
                                file_md5=hashlib.md5(b"deleted").hexdigest(), file_type=".txt", status="deleted")
        db.add(deleted); db.flush()
        db.add(FaqPair(household_id=demo_home, question="deleted secret", answer="hidden",
                       source_file_id=deleted.id, status="rejected"))
    assert client.get(f"/api/tickets/my/{demo_ticket['id']}", headers=bearer(other["access_token"])).status_code == 404
    repository = ToolRepositories(app.state.database.session_factory)
    assert repository.approved_faq(other_home, "deleted secret") == []
    assert repository.approved_faq(demo_home, "deleted secret") == []


def test_official_knowledge_search_proves_household_scoped_vector_result(client, app, settings):
    from langchain_core.documents import Document

    from backend.tests.test_official_device_agent import DeterministicToolModel, _official_context

    with app.state.database.session_factory() as db:
        from backend.app.db.models import Account, HouseholdMember
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        account_id = demo.id

    class ScopedVectorService:
        def __init__(self):
            self.calls = []
            self.violations = []

        def search(self, query, k=4, household_id=None, skill_version_ids=()):
            self.calls.append({"query": query, "household_id": household_id,
                               "skill_version_ids": tuple(skill_version_ids or ())})
            if not household_id or not skill_version_ids:
                self.violations.append({"household_id": household_id, "skill_version_ids": skill_version_ids})
                return []
            if household_id != demo_home:
                return []
            return [Document(page_content="向量知识：上门检修预约流程仅本家庭可见")]

    vector = ScopedVectorService()
    app.state.vector_store_service = vector
    context = _official_context(app, account_id,
                                DeterministicToolModel(tool_name="device.knowledge.search",
                                                       arguments={"query": "检修预约"}))
    events = list(app.state.deployment_agent_runner.stream([{"role": "user", "content": "查询检修"}], context))
    content = "".join(event.get("data", {}).get("content", "") for event in events)
    assert "上门检修预约流程仅本家庭可见" in content, events
    assert vector.calls and vector.calls[0]["household_id"] == demo_home
    assert vector.calls[0]["skill_version_ids"]
    assert vector.violations == []


def test_installation_grant_and_revoke_are_audited(client, app, settings):
    from backend.app.db.models import Account, AgentInstallation, HouseholdMember, ToolDefinition

    demo = login(client, "demo", settings.demo_initial_password)
    with app.state.database.session_factory() as db:
        demo_account = db.scalar(select(Account).where(Account.username == "demo"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo_account.id))
        installation = db.scalar(select(AgentInstallation).where(AgentInstallation.household_id == demo_home,
                                                                  AgentInstallation.authority_slot == "active"))
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "device.restock.register"))
        installation_id, tool_id, principal_id = installation.id, tool.id, demo_account.id

    granted = client.put(f"/api/installations/{installation_id}/permissions", headers=bearer(demo["access_token"]),
                         json={"tool_id": tool_id, "tool_version": "1"})
    assert granted.status_code == 201
    revoked = client.delete(f"/api/installations/{installation_id}/permissions/{tool_id}",
                            headers=bearer(demo["access_token"]))
    assert revoked.status_code == 204
    with app.state.database.session_factory() as db:
        from backend.app.db.models import AdminActionAudit
        actions = {row.action: row for row in db.scalars(select(AdminActionAudit).where(
            AdminActionAudit.principal_id == principal_id,
            AdminActionAudit.resource_id == tool_id)).all()}
        assert set(actions) == {"installation.grant", "installation.revoke"}
        assert all(row.household_id == demo_home for row in actions.values())


def _run_alembic(tmp_path, target, name):
    database_path = tmp_path / name
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    result = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                            cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)
    return database_path, result


def _legacy_statements(engine, households=1, membership=True, device_home="home-1", ticket=True):
    from sqlalchemy import text
    statements = [
        "INSERT INTO accounts (id, username, password_hash, role, status, must_change_password, created_at)"
        " VALUES ('acc-1','legacy','x','user','active',0,'2026-01-01 00:00:00')",
        "INSERT INTO households (id, name, created_by_account_id, created_at)"
        " VALUES ('home-1','Legacy Home','acc-1','2026-01-01 00:00:00')",
    ]
    if households > 1:
        statements.append(
            "INSERT INTO accounts (id, username, password_hash, role, status, must_change_password, created_at)"
            " VALUES ('acc-2','legacy2','x','user','active',0,'2026-01-02 00:00:00')")
        statements.append(
            "INSERT INTO households (id, name, created_by_account_id, created_at)"
            " VALUES ('home-2','Second Home','acc-2','2026-01-02 00:00:00')")
        statements.append(
            "INSERT INTO household_members (id, household_id, account_id, role, joined_at)"
            " VALUES ('mem-2','home-2','acc-2','owner','2026-01-02 00:00:00')")
    if membership:
        statements.append(
            "INSERT INTO household_members (id, household_id, account_id, role, joined_at)"
            " VALUES ('mem-1','home-1','acc-1','owner','2026-01-01 00:00:00')")
    statements.append(
        "INSERT INTO devices (id, household_id, model_id, model_name, category, specs, description, price, features)"
        f" VALUES ('dev-1','{device_home}','LEGACY','旧设备','c','s','d',1,'f')")
    if ticket:
        statements.append(
            "INSERT INTO tickets (household_id, account_id, title, summary, priority, status, created_at)"
            " VALUES ('home-1','acc-1','旧工单','旧工单','normal','open','2026-01-01 00:00:00')")
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def test_single_household_legacy_rows_upgrade_succeeds(tmp_path):
    from sqlalchemy import create_engine, inspect

    database_path, result = _run_alembic(tmp_path, "20260828_0009", "legacy-single.db")
    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    _legacy_statements(engine, households=1, membership=True, device_home="home-1", ticket=True)
    engine.dispose()
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                              cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    assert {tuple(item["column_names"]) for item in inspect(engine).get_unique_constraints("knowledge_files")} == {
        ("household_id", "file_md5"), ("id", "household_id")}


def test_ambiguous_legacy_membership_upgrade_fails_fast(tmp_path):
    from sqlalchemy import create_engine

    database_path, result = _run_alembic(tmp_path, "20260828_0009", "legacy-membership.db")
    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    _legacy_statements(engine, households=1, membership=False, device_home="home-1", ticket=True)
    engine.dispose()
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                              cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)
    assert upgraded.returncode != 0
    assert "DEVICE_LEGACY_MEMBERSHIP_REPAIR_REQUIRED" in upgraded.stderr


def test_ambiguous_multi_household_device_attribution_fails_fast(tmp_path):
    from sqlalchemy import create_engine

    database_path, result = _run_alembic(tmp_path, "20260828_0009", "legacy-attribution.db")
    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    _legacy_statements(engine, households=2, membership=True, device_home="home-1", ticket=False)
    engine.dispose()
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                              cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)
    assert upgraded.returncode != 0
    assert "DEVICE_LEGACY_ATTRIBUTION_AMBIGUOUS" in upgraded.stderr


def test_md5_migration_roundtrip_restores_global_unique_on_downgrade(tmp_path):
    from sqlalchemy import create_engine, inspect

    database_path, result = _run_alembic(tmp_path, "head", "roundtrip.db")
    assert result.returncode == 0, result.stderr
    environment = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{database_path}"}
    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "downgrade", "20260828_0009"],
        cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)
    assert downgraded.returncode == 0, downgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    assert {("file_md5",)} <= {tuple(item["column_names"]) for item in inspect(engine).get_unique_constraints("knowledge_files")}
    engine.dispose()
    upgraded = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                              cwd=Path(__file__).parents[2], env=environment, capture_output=True, text=True)
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    assert {tuple(item["column_names"]) for item in inspect(engine).get_unique_constraints("knowledge_files")} == {
        ("household_id", "file_md5"), ("id", "household_id")}
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "20260828_0017"


def test_two_households_seed_same_knowledge_md5_at_head(tmp_path, settings):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    database_path, result = _run_alembic(tmp_path, "head", "two-households.db")
    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    _legacy_statements(engine, households=1, membership=True, device_home="home-1", ticket=True)
    from backend.app.db.seed import _seed_knowledge
    session_factory = sessionmaker(engine)
    with session_factory.begin() as session:
        _seed_knowledge(session, Path(settings.knowledge_path), "home-1")
        _seed_knowledge(session, Path(settings.knowledge_path), "home-2")
    with session_factory() as session:
        from sqlalchemy import func as func_
        from backend.app.db.models import KnowledgeFile
        duplicated = session.scalar(select(func_.count()).select_from(KnowledgeFile).group_by(
            KnowledgeFile.file_md5).order_by(func_.count().desc()))
        assert duplicated >= 2
