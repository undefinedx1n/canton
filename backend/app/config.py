"""应用配置：全部通过环境变量或 .env 覆盖，便于 Docker Compose 与本地开发保持一致。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.getenv("SCHEDULE_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_name: str = "游乐园排班系统"
    # 默认 SQLite（开箱即用）；生产改为 postgresql+psycopg://user:pass@host:5432/schedule
    database_url: str = f"sqlite:///{(DATA_DIR / 'schedule.db').as_posix()}"
    jwt_secret: str = "change-me-in-production-please-use-env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # 求解器
    solver_max_time_seconds: float = 20.0
    solver_random_seed: int = 20261105
    # 首个管理员
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin123456"
    # 是否允许生成“带缺口草稿”（需求文档 17.5 待确认项，做成配置）
    allow_gap_draft: bool = True

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
