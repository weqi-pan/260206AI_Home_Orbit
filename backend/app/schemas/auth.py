from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _bcrypt_password(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("密码的 UTF-8 编码不能超过 72 字节")
    return value


BcryptPassword = Annotated[str, AfterValidator(_bcrypt_password)]


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: BcryptPassword = Field(min_length=8, max_length=128)
    city: str = Field(default="上海", max_length=100)
    phone: str | None = Field(default=None, max_length=32)


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: BcryptPassword = Field(min_length=8, max_length=128)


class AccountResponse(BaseModel):
    id: str
    username: str
    role: str
    status: str
    must_change_password: bool
    user_id: str | None

    model_config = ConfigDict(from_attributes=True)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    account: AccountResponse


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
