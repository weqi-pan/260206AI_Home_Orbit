import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "智能客服系统 API"
    database_url: str = "mysql+pymysql://root:password@localhost:3306/customer_service"
    sqlite_fallback_url: str = "sqlite:///backend/data/customer_service.db"
    jwt_secret: str = "development-only-change-this-secret"
    access_token_minutes: int = 120
    refresh_token_days: int = 7
    bcrypt_rounds: int = 12
    admin_initial_password: str = "Admin@123"
    demo_initial_password: str = "Demo@1234"
    cors_origins: str = "http://localhost:5173"
    usage_csv_path: Path = Path("data/external/records.csv")
    knowledge_path: Path = Path("data")
    knowledge_upload_path: Path = Path("backend/data/knowledge")
    chroma_path: Path = Path("backend/data/chroma")
    main_model_name: str = "qwen3-max"
    aux_model_name: str = "qwen-flash"

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
