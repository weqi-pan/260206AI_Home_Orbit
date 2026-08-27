from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.security import create_token, get_current_account, get_db, hash_password, verify_password
from backend.app.db.models import Account, Household, HouseholdMember, User
from backend.app.schemas.auth import AccessToken, AccountResponse, LoginRequest, PasswordRequest, RefreshRequest, RegisterRequest, TokenPair


router = APIRouter(prefix="/auth", tags=["auth"])
REGISTER_ID_RETRIES = 3


def _tokens(account: Account, request: Request) -> TokenPair:
    settings = request.app.state.settings
    return TokenPair(
        access_token=create_token(account, settings.jwt_secret, "access", timedelta(minutes=settings.access_token_minutes)),
        refresh_token=create_token(account, settings.jwt_secret, "refresh", timedelta(days=settings.refresh_token_days)),
        account=AccountResponse.model_validate(account),
    )


def _next_user_id(session: Session) -> str:
    max_user_id = session.scalar(select(func.max(func.cast(User.user_id, Integer)))) or 1001
    return str(max(max_user_id + 1, 1002))


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, session: Session = Depends(get_db)):
    if session.scalar(select(Account).where(Account.username == payload.username)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    password_hash = hash_password(payload.password, request.app.state.settings.bcrypt_rounds)
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
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if session.scalar(select(Account).where(Account.username == payload.username)):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from exc
            if attempt == REGISTER_ID_RETRIES - 1:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户编号分配冲突，请重试") from exc
            continue
        session.refresh(account)
        return _tokens(account, request)


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, session: Session = Depends(get_db)):
    account = session.scalar(select(Account).where(Account.username == payload.username))
    if not account or account.status != "active" or not verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return _tokens(account, request)


@router.post("/refresh", response_model=AccessToken)
def refresh(payload: RefreshRequest, request: Request, session: Session = Depends(get_db)):
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或已过期的刷新令牌")
    try:
        claims = jwt.decode(payload.refresh_token, request.app.state.settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    if claims.get("type") != "refresh":
        raise unauthorized
    account = session.get(Account, claims.get("sub"))
    if not account or account.status != "active":
        raise unauthorized
    settings = request.app.state.settings
    return AccessToken(access_token=create_token(
        account, settings.jwt_secret, "access", timedelta(minutes=settings.access_token_minutes)
    ))


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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码错误")
    account.password_hash = hash_password(payload.new_password, request.app.state.settings.bcrypt_rounds)
    account.must_change_password = False
    session.add(account)
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
