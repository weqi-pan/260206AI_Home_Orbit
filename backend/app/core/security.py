from datetime import datetime, timedelta, timezone
from functools import lru_cache
import uuid

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.db.models import Account


bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))


@lru_cache(maxsize=4)
def _dummy_hash(rounds: int) -> str:
    return hash_password("dummy-password-for-timing-alignment", rounds)


def verify_dummy_password(password: str, rounds: int = 12) -> None:
    """登录失败路径也跑一次 bcrypt，让「用户不存在」和「密码错误」耗时接近。

    不做这一步的话，攻击者可以用响应时间区分「用户名存在」与「不存在」，
    409 上做的枚举收窄会从耗时侧漏回来。
    """
    try:
        verify_password(password, _dummy_hash(rounds))
    except ValueError:
        pass


def create_token(account: Account, secret: str, token_type: str, expires_delta: timedelta, *,
                 token_id: str | None = None, family_id: str | None = None) -> str:
    """签发 JWT。

    `ver` 写入账号当前的 `token_version`：改密 / 管理员重置 / 禁用会把版本号 +1，
    于是所有旧访问令牌在 `get_current_account` 处一并失效。
    新参数一律 keyword-only 且有默认值 —— 既有位置参数调用保持不变。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": account.id,
        "role": account.role,
        "type": token_type,
        "ver": account.token_version or 0,
        # 每个令牌都带 jti：既让同一秒内签发的令牌互不相同（iat/exp 是秒级，
        # 否则轮换出来的 access 会和旧的逐字节相同），也给后续吊销留了抓手
        "jti": token_id or uuid.uuid4().hex,
        "iat": now,
        "exp": now + expires_delta,
    }
    if family_id:
        payload["fid"] = family_id
    return jwt.encode(payload, secret, algorithm="HS256")


def get_db(request: Request):
    with request.app.state.database.session_factory() as session:
        yield session


def get_current_account(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> Account:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                 detail={"code": "ACCESS_TOKEN_INVALID", "message": "无效或已过期的访问令牌"})
    if not credentials:
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials, request.app.state.settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    if payload.get("type") != "access":
        raise unauthorized
    account = session.get(Account, payload.get("sub"))
    if not account or account.status != "active":
        raise unauthorized
    # 缺省 0：升级前签发的在途令牌没有 ver，仍按版本 0 放行，避免升级即全员掉线
    if payload.get("ver", 0) != (account.token_version or 0):
        raise unauthorized
    return account


def require_admin(account: Account = Depends(get_current_account)) -> Account:
    if account.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail={"code": "ADMIN_REQUIRED", "message": "需要管理员权限"})
    return account
