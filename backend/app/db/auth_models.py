"""认证域的表：刷新令牌会话与登录尝试记录。

与 `models.py` 的 V1 表分开存放，避免把认证细节继续堆进已近千行的兼容模块。
`models.py` 尾部按既有「Staged V2 compatibility exports」风格 re-export，
既有 `from backend.app.db.models import ...` 的导入路径不受影响。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, utcnow, uuid_str


class RefreshSession(Base):
    """一次刷新令牌的签发记录。

    `token_id` 就是 refresh JWT 的 `jti`；`family_id` 把「登录 → 刷新 → 刷新」
    串成一条链，一旦检测到重放就整族吊销。
    """

    __tablename__ = "refresh_sessions"
    __table_args__ = (
        Index("ix_refresh_sessions_account_expires", "account_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    token_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    replaced_by_token_id: Mapped[str | None] = mapped_column(String(64))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    # 只留够排障的长度，不存完整 UA：它可能带上设备型号等不必要的个人信息
    user_agent: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LoginAttempt(Base):
    """登录/注册/刷新的尝试流水，限流按 `key_hash` 在时间窗内计数。

    `key_hash` 是 `sha256(scope:identifier)`，**不落用户名或 IP 原文** ——
    验收用例会逐表扫描明文凭据，这张表每天都在写，更不能存可读的身份标识。
    """

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_key_created", "key_hash", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
