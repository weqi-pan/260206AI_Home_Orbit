import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DATA_ROOT = REPO_ROOT / "backend" / "data"


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
    main_model_name: str = "qwen3-max"
    aux_model_name: str = "qwen-flash"
    followup_timeout_seconds: float = 4.0

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
