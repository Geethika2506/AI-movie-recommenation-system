from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CINEMATCH_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/cinematch.db"
    data_dir: Path = Path("data")
    artifact_dir: Path = Path("artifacts")
    # Comma-separated in .env (not JSON); list[str] triggers JSON parsing.
    cors_origins: str = "http://localhost:5173"
    read_only: bool = False
    log_level: str = "INFO"
    tmdb_token: str | None = None
    tmdb_cache_ttl_seconds: int = 86_400
    poster_lookup: bool = True
    public_app_url: str = "http://localhost:5173"
    auto_create_schema: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def normalize_cors_origins(cls, value: object) -> str:
        if isinstance(value, list):
            return ",".join(str(part).strip() for part in value if str(part).strip())
        if value is None:
            return "http://localhost:5173"
        return str(value).strip()

    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
