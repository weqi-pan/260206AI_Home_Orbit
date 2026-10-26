"""健康探针与 Prometheus 指标。

`/health` 保留原有的 `status` 键（容器的健康检查依赖它），并补充 `checks`：
- `database`：`SELECT 1`，连不上就是 `degraded`；
- `revision`：当前迁移版本。测试/预览走 `create_all()`，没有 alembic_version 表，报 `None`；
- `redis`：只有 production 才探（其余模式根本没有 Redis），失败记 `degraded` 而不是 500 ——
  探针挂掉不该让单点故障升级成整体不可用。
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from backend.app.db.session import current_database_revision


logger = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])


def _database_check(database) -> str:
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("健康检查：数据库连接失败", exc_info=True)
        return "unavailable"
    return "ok"


def _revision_check(database, app_mode: str) -> str | None:
    if app_mode in {"test", "preview"}:
        # 这两种模式走 create_all()，库里没有 alembic_version 表，探它没有意义
        return None
    try:
        return current_database_revision(database.engine)
    except Exception:
        logger.warning("健康检查：读取迁移版本失败", exc_info=True)
        return "unavailable"


def _redis_check(redis_url: str) -> str:
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        logger.warning("健康检查：Redis 不可达", exc_info=True)
        return "unavailable"
    return "ok"


@router.get("/health")
def health(request: Request):
    database = request.app.state.database
    settings = request.app.state.settings
    checks = {
        "database": _database_check(database),
        "revision": _revision_check(database, settings.app_mode),
        "redis": _redis_check(settings.redis_url) if settings.app_mode == "production" else "skipped",
    }
    degraded = any(value not in {None, "ok", "skipped"} for value in checks.values())
    return {"status": "degraded" if degraded else "ok", "checks": checks}


@router.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
