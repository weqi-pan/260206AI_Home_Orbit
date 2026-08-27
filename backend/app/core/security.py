from datetime import datetime, timedelta, timezone

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


def create_token(account: Account, secret: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": account.id, "role": account.role, "type": token_type, "iat": now, "exp": now + expires_delta},
        secret,
        algorithm="HS256",
    )


def get_db(request: Request):
    with request.app.state.database.session_factory() as session:
        yield session


def get_current_account(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> Account:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或已过期的访问令牌")
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
    return account


def require_admin(account: Account = Depends(get_current_account)) -> Account:
    if account.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return account
