from datetime import timedelta
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.rate_limit import client_ip
from backend.app.core.security import (create_token, get_current_account, get_db, hash_password, verify_dummy_password,
                                       verify_password)
from backend.app.db.models import Account, Household, HouseholdMember, User
from backend.app.schemas.auth import (AccountResponse, LoginRequest, LogoutRequest, PasswordRequest, RefreshRequest,
                                      RegisterRequest, TokenPair)
from backend.app.services import auth_sessions


router = APIRouter(prefix="/auth", tags=["auth"])
REGISTER_ID_RETRIES = 3


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": code, "message": message})


def _rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "LOGIN_RATE_LIMITED", "message": "尝试次数过多，请稍后重试"},
        headers={"Retry-After": str(retry_after)},
    )


def _limiter(request: Request):
    return getattr(request.app.state, "rate_limiter", None)


def _ensure_below(request: Request, session: Session, scope: str, identifier: str, limit: int) -> None:
    """超限即 429。必须在任何昂贵操作（bcrypt、DB 写入）之前调用。"""
    settings = request.app.state.settings
    limiter = _limiter(request)
    if not settings.auth_rate_limit_enabled or limiter is None or limit <= 0:
        return
    if limiter.count(session, scope, identifier) >= limit:
        raise _rate_limited(settings.auth_rate_limit_window_seconds)


def _note_attempt(request: Request, session: Session, scope: str, identifier: str) -> None:
    """记一次尝试。

    必须立刻提交：登录失败会抛 401，异常路径下 `get_db` 的会话不会提交，
    计数就白记了 —— 限流也就等于没有。
    """
    settings = request.app.state.settings
    limiter = _limiter(request)
    if not settings.auth_rate_limit_enabled or limiter is None:
        return
    limiter.record(session, scope, identifier)


def _clear_attempts(request: Request, session: Session, scope: str, identifier: str) -> None:
    limiter = _limiter(request)
    if limiter is not None:
        limiter.clear(session, scope, identifier)


def _issue_tokens(account: Account, request: Request, session: Session,
                  *, family_id: str | None = None) -> tuple[TokenPair, str]:
    """签发一对令牌，并把 refresh 的 jti 记进 refresh_sessions；返回 (令牌对, 新 token_id)。"""
    settings = request.app.state.settings
    token_id = auth_sessions.new_token_id()
    family = family_id or auth_sessions.new_token_id()
    auth_sessions.record_session(
        session, token_id=token_id, family_id=family, account_id=account.id,
        expires_at=auth_sessions.refresh_expiry(settings.refresh_token_days),
        client_ip=client_ip(request, settings), user_agent=request.headers.get("user-agent"),
    )
    pair = TokenPair(
        access_token=create_token(account, settings.jwt_secret, "access",
                                  timedelta(minutes=settings.access_token_minutes)),
        refresh_token=create_token(account, settings.jwt_secret, "refresh",
                                   timedelta(days=settings.refresh_token_days), token_id=token_id, family_id=family),
        account=AccountResponse.model_validate(account),
    )
    return pair, token_id


def _next_user_id(session: Session) -> str:
    max_user_id = session.scalar(select(func.max(func.cast(User.user_id, Integer)))) or 1001
    return str(max(max_user_id + 1, 1002))


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, session: Session = Depends(get_db)):
    settings = request.app.state.settings
    ip = client_ip(request, settings)
    # 注册按「每一次请求」计数（不只是失败）：要挡的是批量注册，成功本身就是要挡的东西
    _ensure_below(request, session, "register_ip", ip, settings.register_rate_limit_ip_attempts)
    _note_attempt(request, session, "register_ip", ip)
    # 不透露「用户名已存在」：文案统一成「无法完成注册」，让枚举者拿不到可靠信号
    # （原来直接说「用户名已存在」等于送了一个账号探测接口）。
    unavailable = HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={"code": "USERNAME_UNAVAILABLE", "message": "无法完成注册，请更换用户名或稍后重试"})
    if session.scalar(select(Account).where(Account.username == payload.username)):
        raise unavailable
    password_hash = hash_password(payload.password, settings.bcrypt_rounds)
    for attempt in range(REGISTER_ID_RETRIES):
        user_id = _next_user_id(session)
        user = User(user_id=user_id, username=payload.username, city=payload.city, phone=payload.phone)
        account = Account(username=payload.username, password_hash=password_hash, user_id=user_id)
        session.add_all([user, account])
        try:
            session.flush()
            household = Household(name=f"{payload.username}的家庭", created_by_account_id=account.id)
            session.add(household)
            session.flush()
            session.add(HouseholdMember(household_id=household.id, account_id=account.id, role="owner"))
            from backend.app.db.seed import _seed_devices, _seed_knowledge, seed_official_device_agent
            _seed_devices(session, household.id)
            _seed_knowledge(session, Path(settings.knowledge_path), household.id)
            seed_official_device_agent(session, household.id, account.id)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if session.scalar(select(Account).where(Account.username == payload.username)):
                raise unavailable from exc
            if attempt == REGISTER_ID_RETRIES - 1:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail={"code": "USER_ID_CONFLICT", "message": "用户编号分配冲突，请重试"}) from exc
            continue
        session.refresh(account)
        tokens, _token_id = _issue_tokens(account, request, session)
        session.commit()
        return tokens


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, session: Session = Depends(get_db)):
    settings = request.app.state.settings
    ip = client_ip(request, settings)
    # 先按用户名限流：同一个账号被反复试密码，不用等 IP 档位就能拦住
    _ensure_below(request, session, "login_username", payload.username, settings.login_rate_limit_username_attempts)
    _ensure_below(request, session, "login_ip", ip, settings.login_rate_limit_ip_attempts)
    account = session.scalar(select(Account).where(Account.username == payload.username))
    if not account or account.status != "active":
        # 用户不存在 / 被禁用也要跑一次 bcrypt，否则耗时差把「账号是否存在」泄出去
        verify_dummy_password(payload.password, settings.bcrypt_rounds)
        _note_attempt(request, session, "login_username", payload.username)
        _note_attempt(request, session, "login_ip", ip)
        raise _unauthorized("AUTH_INVALID_CREDENTIALS", "用户名或密码错误")
    if not verify_password(payload.password, account.password_hash):
        _note_attempt(request, session, "login_username", payload.username)
        _note_attempt(request, session, "login_ip", ip)
        raise _unauthorized("AUTH_INVALID_CREDENTIALS", "用户名或密码错误")
    # 只清用户名维度：IP 维度成功也清的话，攻击者用自己账号成功登录一次就能重置 IP 计数
    _clear_attempts(request, session, "login_username", payload.username)
    session.flush()
    tokens, _token_id = _issue_tokens(account, request, session)
    session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, request: Request, session: Session = Depends(get_db)):
    settings = request.app.state.settings
    invalid = _unauthorized("REFRESH_TOKEN_INVALID", "无效或已过期的刷新令牌")
    try:
        claims = jwt.decode(payload.refresh_token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise invalid from exc
    if claims.get("type") != "refresh":
        raise invalid
    account = session.get(Account, claims.get("sub"))
    if not account or account.status != "active":
        raise invalid

    token_id = claims.get("jti")
    family_id = claims.get("fid")
    if not token_id or not family_id:
        # 升级前签发的老令牌没有 jti：允许一次性换成新格式，但不参与轮换检测
        tokens, _token_id = _issue_tokens(account, request, session)
        session.commit()
        return tokens

    state, _existing = auth_sessions.consume_refresh(session, token_id)
    if state == "reused":
        # 重放：整族吊销 + 提升版本号，把「可能已经被别人用掉的那条链」彻底掐断
        auth_sessions.revoke_family(session, family_id)
        auth_sessions.bump_token_version(session, account)
        session.commit()
        raise _unauthorized("REFRESH_REUSED", "刷新令牌已被使用，请重新登录")
    if state == "revoked":
        session.commit()
        raise invalid
    if state == "unknown":
        # 没有记录的令牌：可能是伪造，也可能是刷新会话表上线前签发的。后者仍然有效，
        # 但既然拿不到 jti 记录，就让客户端重新登录，避免绕过轮换检测。
        raise invalid

    tokens, new_token_id = _issue_tokens(account, request, session, family_id=family_id)
    auth_sessions.mark_replaced(session, token_id, new_token_id)
    session.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, request: Request, response: Response, session: Session = Depends(get_db)):
    """吊销单个 refresh 令牌。永远 204 —— 登出不该因为令牌已经失效而报错。"""
    settings = request.app.state.settings
    try:
        claims = jwt.decode(payload.refresh_token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        claims = {}
    token_id = claims.get("jti")
    if token_id:
        auth_sessions.revoke_token(session, token_id)
        session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(response: Response, account: Account = Depends(get_current_account),
               session: Session = Depends(get_db)):
    """吊销该账号全部会话，并让所有在途访问令牌立即失效。"""
    auth_sessions.revoke_all_for_account(session, account.id)
    auth_sessions.bump_token_version(session, account)
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT


@router.get("/me", response_model=AccountResponse)
def me(account: Account = Depends(get_current_account)):
    return account


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordRequest,
    request: Request,
    response: Response,
    account: Account = Depends(get_current_account),
    session: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, account.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"code": "PASSWORD_MISMATCH", "message": "当前密码错误"})
    account.password_hash = hash_password(payload.new_password, request.app.state.settings.bcrypt_rounds)
    account.must_change_password = False
    # 改密后旧令牌必须立刻失效：否则泄露的访问令牌能继续用到过期
    auth_sessions.bump_token_version(session, account)
    auth_sessions.revoke_all_for_account(session, account.id)
    session.add(account)
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
