import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db.models import Base


@dataclass
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]
    is_fallback: bool = False

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


def _is_mysql_connectivity_error(exc: Exception) -> bool:
    if isinstance(exc, OSError):
        return True
    if not isinstance(exc, OperationalError):
        return False
    original = exc.orig
    if isinstance(original, (OSError, TimeoutError)):
        return True
    args = getattr(original, "args", ())
    if args and args[0] in {2002, 2003, 2005, 2006, 2013}:
        return True
    message = str(original).lower()
    return any(text in message for text in ("connection refused", "timed out", "timeout", "can't connect"))


def create_database(
    settings: Settings,
    mysql_probe: Callable[[URL], None] | None = None,
) -> Database:
    primary_url = _mysql_utf8mb4_url(settings.database_url)
    is_mysql = primary_url.drivername.startswith("mysql")
    if is_mysql:
        try:
            if mysql_probe:
                mysql_probe(primary_url)
                engine = _engine(primary_url)
            else:
                engine = _engine(primary_url)
                with engine.connect() as connection:
                    connection.exec_driver_sql("SELECT 1")
        except Exception as exc:
            if not _is_mysql_connectivity_error(exc):
                raise
            logging.getLogger(__name__).warning("MySQL 连接失败，自动降级 SQLite: %s", exc)
            engine = _engine(settings.sqlite_fallback_url)
            return Database(engine, sessionmaker(engine, expire_on_commit=False), True)
    else:
        engine = _engine(primary_url)
    return Database(engine, sessionmaker(engine, expire_on_commit=False), False)
