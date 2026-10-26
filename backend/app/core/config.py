import base64
import binascii
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DATA_ROOT = REPO_ROOT / "backend" / "data"

# 将仓库根 .env 装载进进程环境（不覆盖已有变量）：保证 LLM_CREDENTIAL_MASTER_KEY 等
# 通过 os.getenv 读取的配置在本地 uvicorn/celery/alembic 启动时与 Docker 部署行为一致。
load_dotenv(REPO_ROOT / ".env", override=False)


class Settings(BaseSettings):
    app_name: str = "智能客服系统 API"
    app_mode: Literal["production", "preview", "test"] = "production"
    database_url: str = "mysql+pymysql://root:password@localhost:3306/customer_service"
    jwt_secret: str = "development-only-change-this-secret"
    access_token_minutes: int = 60
    refresh_token_days: int = 7
    bcrypt_rounds: int = 12
    admin_initial_password: str = "Admin@123"
    demo_initial_password: str = "Demo@1234"
    cors_origins: str = "http://localhost:5173"
    usage_csv_path: Path = REPO_ROOT / "data" / "external" / "records.csv"
    knowledge_path: Path = REPO_ROOT / "data"
    knowledge_upload_path: Path = BACKEND_DATA_ROOT / "knowledge"
    chroma_path: Path = BACKEND_DATA_ROOT / "chroma"
    followup_timeout_seconds: float = 4.0
    redis_url: str = "redis://localhost:6379/0"
    celery_retry_backoff_seconds: int = 30
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # 认证限流：登录只对**失败**计数；注册按每次请求计数（要挡的就是批量注册）
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_backend: Literal["database", "memory"] = "database"
    auth_rate_limit_window_seconds: int = 300
    login_rate_limit_username_attempts: int = 10
    login_rate_limit_ip_attempts: int = 30
    register_rate_limit_ip_attempts: int = 10
    # 只有确实部署在可信反代后面才打开：X-Forwarded-For 由客户端伪造
    auth_rate_limit_trust_forwarded_for: bool = False

    # 高风险审批的有效窗口：客户端可以传更短的值，但服务端会夹到 max 以内，
    # 否则一句「批准十年」就能把「高风险需逐次确认」整个绕开
    approval_max_minutes: int = 10
    approval_default_minutes: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


def validate_production_settings(settings: "Settings") -> None:
    """生产模式启动前拒绝可预测或缺失的安全配置。"""
    errors: list[str] = []

    if not settings.database_url:
        errors.append("DATABASE_URL 不能为空")
    if len(settings.jwt_secret) < 32 or settings.jwt_secret == "development-only-change-this-secret":
        errors.append("JWT_SECRET 必须设置且不少于 32 字符")
    if len(settings.admin_initial_password) < 12 or settings.admin_initial_password == "Admin@123":
        errors.append("ADMIN_INITIAL_PASSWORD 必须设置且不少于 12 字符")
    if len(settings.demo_initial_password) < 12 or settings.demo_initial_password == "Demo@1234":
        errors.append("DEMO_INITIAL_PASSWORD 必须设置且不少于 12 字符")

    master_key = os.getenv("LLM_CREDENTIAL_MASTER_KEY")
    if not master_key:
        errors.append("LLM_CREDENTIAL_MASTER_KEY 不能为空")
    else:
        try:
            decoded = base64.b64decode(master_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            errors.append("LLM_CREDENTIAL_MASTER_KEY 必须是有效的 Base64 编码 32 字节值")
            logging.getLogger(__name__).debug("LLM_CREDENTIAL_MASTER_KEY 解码失败", exc_info=exc)
        else:
            if len(decoded) != 32:
                errors.append("LLM_CREDENTIAL_MASTER_KEY 必须是有效的 Base64 编码 32 字节值")

    if errors:
        raise ValueError("生产配置校验失败：" + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.app_mode == "production":
        validate_production_settings(settings)
    elif settings.jwt_secret == "development-only-change-this-secret":
        logging.getLogger(__name__).warning("JWT_SECRET 未设置，正在使用仅限开发环境的默认值")
    return settings
