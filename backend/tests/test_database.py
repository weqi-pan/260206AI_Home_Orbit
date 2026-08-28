import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, NoSuchModuleError, OperationalError


EXPECTED_TABLES = {
    "accounts",
    "users",
    "chat_sessions",
    "tickets",
    "knowledge_files",
    "faq_pairs",
    "restock_notifications",
    "conversation_history",
    "inventory",
    "devices",
    "device_usage_records",
}


def test_create_all_covers_planned_schema(settings):
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()

    assert EXPECTED_TABLES <= set(inspect(database.engine).get_table_names())


def test_production_mysql_connection_failure_is_not_hidden_by_sqlite_fallback(settings):
    from backend.app.db.session import create_database

    settings.app_mode = "production"
    settings.database_url = "mysql+pymysql://user:password@127.0.0.1:1/customer_service"
    attempted_urls = []

    def fail_mysql(url):
        attempted_urls.append(str(url))
        raise OSError("mysql unavailable")

    with pytest.raises(OSError, match="mysql unavailable"):
        create_database(settings, mysql_probe=fail_mysql)

    assert "charset=utf8mb4" in attempted_urls[0]


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("connect", {}, OSError(10061, "connection refused")),
        OperationalError("connect", {}, TimeoutError("connection timed out")),
    ],
)
def test_production_mysql_operational_connectivity_errors_fail_fast(settings, error):
    from backend.app.db.session import create_database

    settings.database_url = "mysql+pymysql://user:password@db/customer_service"
    settings.app_mode = "production"

    with pytest.raises(OperationalError):
        create_database(settings, mysql_probe=lambda _url: (_ for _ in ()).throw(error))


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("connect", {}, Exception(1045, "Access denied for user")),
        OperationalError("connect", {}, Exception(1049, "Unknown database")),
        NoSuchModuleError("Can't load plugin: sqlalchemy.dialects:mysql.missing"),
        ValueError("invalid database configuration"),
    ],
)
def test_mysql_non_connectivity_errors_are_not_hidden_by_fallback(settings, error):
    from backend.app.db.session import create_database

    settings.database_url = "mysql+pymysql://user:password@db/customer_service"
    settings.app_mode = "production"

    with pytest.raises(type(error)):
        create_database(settings, mysql_probe=lambda _url: (_ for _ in ()).throw(error))


def test_sqlite_engine_creates_missing_parent_directory(settings, tmp_path):
    from backend.app.db.session import create_database

    database_path = tmp_path / "nested" / "database" / "app.db"
    settings.database_url = f"sqlite:///{database_path}"

    database = create_database(settings)
    database.create_all()

    assert database_path.exists()


def test_production_mode_rejects_sqlite_database(settings):
    from backend.app.db.session import create_database

    settings.app_mode = "production"

    with pytest.raises(RuntimeError, match="production mode requires MySQL"):
        create_database(settings)


def test_startup_rejects_database_without_required_alembic_revision(settings):
    from backend.app.db.session import create_database, require_database_revision

    database = create_database(settings)

    with pytest.raises(RuntimeError, match="database migrations are not at required revision"):
        require_database_revision(database.engine)


def test_alembic_url_prefers_explicit_override_then_compose_database_url():
    from backend.app.db.migration_url import resolve_migration_url

    mysql_url = "mysql+pymysql://app:secret@mysql:3306/customer_service"
    override_url = "sqlite:///override.db"

    assert resolve_migration_url("sqlite:///configured.db", {"DATABASE_URL": mysql_url}) == mysql_url
    assert resolve_migration_url(
        "sqlite:///configured.db",
        {"DATABASE_URL": mysql_url, "ALEMBIC_DATABASE_URL": override_url},
    ) == override_url


def test_default_settings_paths_are_absolute_and_do_not_create_nested_backend(tmp_path, monkeypatch):
    from pathlib import Path

    from backend.app.core.config import Settings

    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert Path(settings.knowledge_upload_path).is_absolute()
    assert Path(settings.chroma_path).is_absolute()
    assert not (tmp_path / "backend").exists()


def test_seed_is_idempotent_and_imports_demo_data(settings):
    from backend.app.db.models import Account, Device, DeviceUsageRecord, Inventory, User
    from backend.app.db.seed import seed_database
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    seed_database(database.session_factory, settings)
    seed_database(database.session_factory, settings)

    with database.session_factory() as session:
        assert len(session.scalars(select(Account)).all()) == 2
        assert len(session.scalars(select(Device)).all()) == 6
        assert len(session.scalars(select(Inventory)).all()) == 14
        assert session.get(User, "1001").username == "演示用户"
        assert len(session.scalars(select(DeviceUsageRecord)).all()) == 6


def test_seed_does_not_overwrite_existing_device_or_inventory(settings):
    from backend.app.db.models import Account, Device, HouseholdMember, Inventory
    from backend.app.db.seed import seed_database
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    seed_database(database.session_factory, settings)
    with database.session_factory.begin() as session:
        demo = session.scalar(select(Account).where(Account.username == "demo"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        device = session.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == "R1-Lite"))
        device.model_name = "管理员自定义名称"
        inventory = session.scalar(select(Inventory).where(
            Inventory.household_id == household_id, Inventory.model_id == "R1-Lite", Inventory.warehouse == "北京仓"
        ))
        inventory.stock = 999
        inventory.min_stock = 42

    seed_database(database.session_factory, settings)

    with database.session_factory() as session:
        device = session.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == "R1-Lite"))
        assert device.model_name == "管理员自定义名称"
        inventory = session.scalar(select(Inventory).where(
            Inventory.household_id == household_id, Inventory.model_id == "R1-Lite", Inventory.warehouse == "北京仓"
        ))
        assert (inventory.stock, inventory.min_stock) == (999, 42)


def test_preview_mode_uses_explicit_sqlite_database_and_can_seed(settings):
    from backend.app.db.models import Account
    from backend.app.db.seed import seed_database
    from backend.app.db.session import create_database

    settings.app_mode = "preview"
    database = create_database(settings)
    database.create_all()
    seed_database(database.session_factory, settings)

    assert database.engine.url.drivername == "sqlite"
    assert EXPECTED_TABLES <= set(inspect(database.engine).get_table_names())
    with database.session_factory() as session:
        assert {account.username for account in session.scalars(select(Account))} == {"admin", "demo"}


def test_demo_is_linked_to_1001_and_admin_has_no_profile(settings):
    from backend.app.db.models import Account, User
    from backend.app.db.seed import seed_database
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    seed_database(database.session_factory, settings)

    with database.session_factory() as session:
        demo = session.scalar(select(Account).where(Account.username == "demo"))
        admin = session.scalar(select(Account).where(Account.username == "admin"))
        assert demo.user_id == "1001"
        assert session.get(User, "1001") is not None
        assert admin.user_id is None


def test_database_rejects_invalid_account_role(settings):
    from backend.app.db.models import Account
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    with database.session_factory() as session:
        session.add(Account(username="bad-role", password_hash="hash", role="superuser"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_database_rejects_invalid_message_feedback(settings):
    from backend.app.db.models import Account, ChatSession, ConversationHistory
    from backend.app.db.seed import seed_database
    from backend.app.db.session import create_database

    database = create_database(settings)
    database.create_all()
    seed_database(database.session_factory, settings)
    with database.session_factory() as session:
        demo = session.scalar(select(Account).where(Account.username == "demo"))
        from backend.app.db.models import HouseholdMember
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        chat = ChatSession(account_id=demo.id, household_id=household_id)
        session.add(chat)
        session.flush()
        session.add(ConversationHistory(
            session_id=chat.id,
            user_id="1001",
            role="user",
            content="hello",
            feedback="neutral",
        ))
        with pytest.raises(IntegrityError):
            session.commit()
