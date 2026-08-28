from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db.models import Base


REQUIRED_DATABASE_REVISION = "20260828_0014"


@dataclass
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)


def _mysql_utf8mb4_url(raw_url: str) -> URL:
    url = make_url(raw_url)
    if url.drivername.startswith("mysql"):
        return url.update_query_dict({**url.query, "charset": "utf8mb4"})
    return url


def _engine(url: URL | str) -> Engine:
    parsed = make_url(url)
    kwargs = {"pool_pre_ping": True}
    if parsed.drivername == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(parsed, **kwargs)
    if parsed.drivername == "sqlite":
        @event.listens_for(engine, "connect")
        def set_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def create_database(
    settings: Settings,
    mysql_probe: Callable[[URL], None] | None = None,
) -> Database:
    primary_url = _mysql_utf8mb4_url(settings.database_url)
    is_mysql = primary_url.drivername.startswith("mysql")
    if settings.app_mode == "production" and not is_mysql:
        raise RuntimeError("production mode requires MySQL; SQLite is limited to test/preview single-process use")
    if is_mysql:
        if mysql_probe:
            mysql_probe(primary_url)
            engine = _engine(primary_url)
        else:
            engine = _engine(primary_url)
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
    else:
        engine = _engine(primary_url)
    return Database(engine, sessionmaker(engine, expire_on_commit=False))


def require_database_revision(engine: Engine) -> None:
    if "alembic_version" not in inspect(engine).get_table_names():
        raise RuntimeError(f"database migrations are not at required revision {REQUIRED_DATABASE_REVISION}")
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if revision != REQUIRED_DATABASE_REVISION:
        raise RuntimeError(f"database migrations are not at required revision {REQUIRED_DATABASE_REVISION}")
