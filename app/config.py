"""Application settings loaded from .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "MES运维智能体"
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    JWT_SECRET: str = "dev-only-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 12

    DB_PATH: str = "./data/mes_agent.db"
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_MB: int = 20

    MINIMAX_BASE_URL: str = "https://api.minimaxi.com/anthropic"
    MINIMAX_MODEL: str = "minimax-portal/MiniMax-M2.7"
    MINIMAX_API_KEY: str = ""
    MINIMAX_MAX_TOKENS: int = 4096

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "ChangeMe!2026"
    ADMIN_EMAIL: str = "admin@example.com"


settings = Settings()
