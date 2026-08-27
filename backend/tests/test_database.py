from sqlalchemy import inspect, select


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


def test_mysql_url_uses_utf8mb4_and_connection_failure_falls_back_to_sqlite(settings, monkeypatch):
    from backend.app.db.session import create_database

    settings.database_url = "mysql+pymysql://user:password@127.0.0.1:1/customer_service"
    attempted_urls = []

    def fail_mysql(url):
        attempted_urls.append(str(url))
        raise OSError("mysql unavailable")

    database = create_database(settings, mysql_probe=fail_mysql)

    assert "charset=utf8mb4" in attempted_urls[0]
    assert database.is_fallback is True
    assert database.engine.url.drivername == "sqlite"


def test_sqlite_engine_creates_missing_parent_directory(settings, tmp_path):
    from backend.app.db.session import create_database

    database_path = tmp_path / "nested" / "database" / "app.db"
    settings.database_url = f"sqlite:///{database_path}"

    database = create_database(settings)
    database.create_all()

    assert database_path.exists()


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
        assert len(session.scalars(select(Device)).all()) == 3
        assert len(session.scalars(select(Inventory)).all()) == 7
        assert session.get(User, "1001").username == "演示用户"
        assert len(session.scalars(select(DeviceUsageRecord)).all()) == 6
