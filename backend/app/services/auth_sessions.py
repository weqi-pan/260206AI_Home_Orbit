"""刷新令牌的签发、轮换与吊销。

设计要点是**重放即断族**：每次 `/refresh` 用带条件的 UPDATE 把旧令牌标记为
「已使用」，只有真的改到那一行才算轮换成功。如果同一个 refresh JWT 第二次出现，
说明它已经泄露（或者有人在双标签页里并发刷新），此时整族吊销并提升
`token_version`，把攻击者和用户一起请回登录页 —— 比「让两边都继续用」安全得多。
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.db.auth_models import RefreshSession
from backend.app.db.base import utcnow, uuid_str


@dataclass
class IssuedSession:
    token_id: str
    family_id: str


def new_token_id() -> str:
    return uuid_str()


def record_session(
    session: Session, *, token_id: str, family_id: str, account_id: str, expires_at,
    client_ip: str | None, user_agent: str | None,
) -> IssuedSession:
    session.add(RefreshSession(
        token_id=token_id, family_id=family_id, account_id=account_id, expires_at=expires_at,
        client_ip=client_ip, user_agent=(user_agent or "")[:200] or None,
    ))
    return IssuedSession(token_id=token_id, family_id=family_id)


def session_for(session: Session, token_id: str) -> RefreshSession | None:
    return session.scalar(select(RefreshSession).where(RefreshSession.token_id == token_id))


def consume_refresh(session: Session, token_id: str):
    """把令牌标记为已使用。返回 (状态, 原会话)。

    状态：
    - `"ok"`：本次是首次使用，可以继续轮换；
    - `"reused"`：令牌已被用过 —— 视为重放；
    - `"revoked"`：族已被吊销（改密、登出、检测到重放）；
    - `"unknown"`：库里没有这条记录（升级前签发的老令牌，或伪造）。
    """
    existing = session_for(session, token_id)
    if existing is None:
        return "unknown", None
    if existing.revoked_at is not None:
        return "revoked", existing
    # 条件更新是并发安全的关键：两个请求同时到，只有一个能把 used_at 从 NULL 改掉
    result = session.execute(
        update(RefreshSession)
        .where(RefreshSession.token_id == token_id, RefreshSession.used_at.is_(None))
        .values(used_at=utcnow())
    )
    if result.rowcount != 1:
        return "reused", existing
    session.refresh(existing)
    return "ok", existing


def mark_replaced(session: Session, previous_token_id: str, new_token_id: str) -> None:
    session.execute(
        update(RefreshSession)
        .where(RefreshSession.token_id == previous_token_id)
        .values(replaced_by_token_id=new_token_id)
    )


def revoke_family(session: Session, family_id: str) -> int:
    result = session.execute(
        update(RefreshSession)
        .where(RefreshSession.family_id == family_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    return result.rowcount or 0


def revoke_token(session: Session, token_id: str) -> int:
    result = session.execute(
        update(RefreshSession)
        .where(RefreshSession.token_id == token_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    return result.rowcount or 0


def revoke_all_for_account(session: Session, account_id: str) -> int:
    result = session.execute(
        update(RefreshSession)
        .where(RefreshSession.account_id == account_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    return result.rowcount or 0


def bump_token_version(session: Session, account) -> None:
    """让该账号所有在途**访问**令牌立即失效（令牌里带 `ver`）。"""
    account.token_version = (account.token_version or 0) + 1
    session.add(account)


def refresh_expiry(days: int):
    return utcnow() + timedelta(days=days)
