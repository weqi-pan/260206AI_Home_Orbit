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
    access_token_minutes: int = 120
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.jwt_secret == "development-only-change-this-secret":
        logging.getLogger(__name__).warning("JWT_SECRET 未设置，正在使用仅限开发环境的默认值")
    return settings
