"""认证限流。

两档语义，按威胁区分：

- **登录**只统计**失败**尝试：正常用户永远撞不到闸门，而暴力破解必须为此付出代价；
  登录成功会清掉该用户名维度的失败计数。IP 维度成功也清零意义不大反而可被利用
  （拿一个自己的账号成功登录一次就能重置 IP 计数），所以只让它随时间窗自然过期。
- **注册**统计**每一次**尝试：这里要防的是批量注册，成功本身就是要挡的东西。

两档实现：
- `DatabaseRateLimiter`（默认）：把尝试写进 `login_attempts`，多实例部署也能共享；
- `MemoryRateLimiter`：进程内计数，给没有可用数据库的极端场景兜底。

键一律是 `sha256(scope:identifier)`，不落用户名或 IP 原文 —— 这张表每天都在写，
存明文身份标识等于把用户名清单交出去。
"""

import hashlib
import threading
import time
from collections import defaultdict
from datetime import timedelta
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.db.auth_models import LoginAttempt
from backend.app.db.base import utcnow


class RateLimiter(Protocol):
    def count(self, session: Session, scope: str, identifier: str) -> int:
        """窗口内的尝试次数。"""

    def record(self, session: Session, scope: str, identifier: str) -> None:
        """记一次尝试。"""

    def clear(self, session: Session, scope: str, identifier: str) -> None:
        """清零一个键。"""


def attempt_key(scope: str, identifier: str) -> str:
    return hashlib.sha256(f"{scope}:{identifier}".encode("utf-8")).hexdigest()


class DatabaseRateLimiter:
    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = window_seconds

    def count(self, session: Session, scope: str, identifier: str) -> int:
        since = utcnow() - timedelta(seconds=self.window_seconds)
        return session.scalar(
            select(func.count()).select_from(LoginAttempt).where(
                LoginAttempt.key_hash == attempt_key(scope, identifier),
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.created_at >= since,
            )
        ) or 0

    def record(self, session: Session, scope: str, identifier: str) -> None:
        session.add(LoginAttempt(scope=scope, key_hash=attempt_key(scope, identifier), succeeded=False))
        session.commit()

    def clear(self, session: Session, scope: str, identifier: str) -> None:
        session.execute(delete(LoginAttempt).where(LoginAttempt.key_hash == attempt_key(scope, identifier)))


class MemoryRateLimiter:
    """进程内滑动窗口。跨实例不共享，因此只在 `auth_rate_limit_backend="memory"` 时启用。"""

    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = time.monotonic()
        return [stamp for stamp in self._hits.get(key, []) if now - stamp < self.window_seconds]

    def count(self, session: Session, scope: str, identifier: str) -> int:
        with self._lock:
            return len(self._recent(attempt_key(scope, identifier)))

    def record(self, session: Session, scope: str, identifier: str) -> None:
        key = attempt_key(scope, identifier)
        with self._lock:
            recent = self._recent(key)
            recent.append(time.monotonic())
            self._hits[key] = recent

    def clear(self, session: Session, scope: str, identifier: str) -> None:
        with self._lock:
            self._hits.pop(attempt_key(scope, identifier), None)


def build_rate_limiter(settings) -> RateLimiter:
    factory = MemoryRateLimiter if settings.auth_rate_limit_backend == "memory" else DatabaseRateLimiter
    return factory(settings.auth_rate_limit_window_seconds)


def client_ip(request, settings) -> str:
    """取客户端 IP。

    `X-Forwarded-For` 只在显式信任反代时才读：默认不信任，否则攻击者伪造一个
    头就能绕过 IP 维度的限流。
    """
    if settings.auth_rate_limit_trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
