"""进程日志配置。

生产/预览输出单行 JSON（便于日志采集按字段切分），本地预览额外保留人类可读格式。
测试模式刻意不做任何配置：pytest 自己接管 root logger，`caplog` 也依赖 propagate，
在这里改 handler 会把既有断言（如 test_knowledge_bootstrap 的告警捕获）弄坏。
"""

import json
import logging
import logging.config
from datetime import datetime, timezone

from backend.app.core.config import Settings


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """单行 JSON。额外字段（如 `extra={"request_id": ...}`）自动并入。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def configure_logging(settings: Settings) -> None:
    if settings.app_mode == "test":
        return

    formatter = "json" if settings.app_mode == "production" else "console"
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,  # 不能关：uvicorn/sqlalchemy 已建好的 logger 要留着
        "formatters": {
            "json": {"()": f"{__name__}.JsonFormatter"},
            "console": {"format": _CONSOLE_FORMAT},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": formatter,
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["console"], "level": settings.log_level},
        "loggers": {
            # uvicorn 默认自带 handler，关掉 propagate 会重复输出，这里统一交给 root
            "uvicorn": {"handlers": [], "propagate": True, "level": settings.log_level},
            "uvicorn.error": {"handlers": [], "propagate": True, "level": settings.log_level},
            "uvicorn.access": {"handlers": [], "propagate": False, "level": "WARNING"},
        },
    })
