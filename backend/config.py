import os
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Self, TypeGuard

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_caller_supplied_database_url: ContextVar[bool] = ContextVar(
    "cinematch_caller_supplied_database_url", default=False
)
_caller_database_url: ContextVar[str | None] = ContextVar(
    "cinematch_caller_database_url", default=None
)


def _usable_postgres_url(url: str | None) -> TypeGuard[str]:
    if url is None:
        return False
    stripped = url.strip()
    if not stripped:
        return False
    return stripped.startswith(("postgresql://", "postgres://", "postgresql+psycopg://"))


def _psycopg_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


# CI, Compose, and local Postgres do not serve TLS. Remote hosts (Neon, Render) do.
_LOCAL_POSTGRES_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db"})


def _postgres_host_requires_ssl(hostname: str | None) -> bool:
    if not hostname:
        return False
    return hostname.strip("[]").lower() not in _LOCAL_POSTGRES_HOSTS


def _normalize_postgres_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    cleaned = url.strip().strip('"').strip("'")
    cleaned = _psycopg_sqlalchemy_url(cleaned)
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        return cleaned
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "channel_binding"
    ]
    has_sslmode = any(key.lower() == "sslmode" for key, _ in query)
    if not has_sslmode and _postgres_host_requires_ssl(parsed.hostname):
        query.append(("sslmode", "require"))
    return urlunparse(parsed._replace(query=urlencode(query)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CINEMATCH_",
        env_file=(".env", ".env.local"),
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://cinematch:cinematch@localhost:5432/cinematch"
    database_migration_url: str | None = None
    neon_pooled_database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL"),
    )
    neon_direct_database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL_UNPOOLED"),
    )
    data_dir: Path = Path("data")
    artifact_dir: Path = Path("artifacts")
    # Comma-separated in .env (not JSON); list[str] triggers JSON parsing.
    cors_origins: str = "http://localhost:5173"
    read_only: bool = False
    log_level: str = "INFO"
    tmdb_token: str | None = None
    tmdb_cache_ttl_seconds: int = 86_400
    poster_lookup: bool = True
    # When false, only DB + on-disk poster cache are used (no Wikipedia/TMDB on each request).
    poster_live_fetch: bool = True
    public_app_url: str = "http://localhost:5173"
    auto_create_schema: bool = False
    jwt_secret: str = "development-only-change-me-32-bytes"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    secure_cookies: bool = False
    bandit_enabled: bool = False
    bandit_shadow: bool = True
    bandit_epsilon: float = 0.1
    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 20
    redis_url: str | None = None
    recommendation_cache_seconds: int = 90
    recommendation_cache_entries: int = 256
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True

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

    def __init__(self, **data: Any) -> None:
        supplied = "database_url" in data
        supplied_token = _caller_supplied_database_url.set(supplied)
        url_token = _caller_database_url.set(data.get("database_url") if supplied else None)
        try:
            super().__init__(**data)
        finally:
            _caller_supplied_database_url.reset(supplied_token)
            _caller_database_url.reset(url_token)

    @model_validator(mode="after")
    def apply_neon_database_urls(self) -> Self:
        explicit = os.environ.get("CINEMATCH_DATABASE_URL")
        pooled = self.neon_pooled_database_url
        direct = self.neon_direct_database_url
        caller_supplied = _caller_supplied_database_url.get()
        caller_url = _caller_database_url.get()

        # A URL passed into Settings() wins. Tests pass SQLite while CI and local
        # dotenv may export Postgres, including a Neon DATABASE_URL.
        if caller_supplied and _usable_postgres_url(self.database_url):
            self.database_url = _normalize_postgres_url(self.database_url)
        elif not caller_supplied and _usable_postgres_url(pooled) and (
            not explicit or explicit.startswith("sqlite")
        ):
            self.database_url = _normalize_postgres_url(pooled)
        elif not caller_supplied and _usable_postgres_url(explicit) and _usable_postgres_url(
            self.database_url
        ):
            self.database_url = _normalize_postgres_url(self.database_url)

        if caller_supplied and not _usable_postgres_url(caller_url):
            self.database_migration_url = self.database_url
            return self
        if _usable_postgres_url(direct):
            self.database_migration_url = _normalize_postgres_url(direct)
        elif self.database_migration_url and _usable_postgres_url(self.database_migration_url):
            self.database_migration_url = _normalize_postgres_url(self.database_migration_url)
        elif self.database_migration_url is None:
            self.database_migration_url = self.database_url
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
