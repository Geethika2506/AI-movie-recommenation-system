from pathlib import Path

import pytest

from backend.config import Settings

CI_DATABASE_URL = "postgresql+psycopg://cinematch:cinematch@localhost:5432/cinematch"


def load_settings(database_url: str | None = None) -> Settings:
    # Ignore developer dotenv files so these cases see only the env vars they set.
    if database_url is None:
        return Settings(_env_file=None)
    return Settings(_env_file=None, database_url=database_url)


@pytest.fixture
def clear_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CINEMATCH_DATABASE_URL", raising=False)
    monkeypatch.delenv("CINEMATCH_DATABASE_MIGRATION_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)


def test_ci_localhost_url_does_not_require_ssl(
    monkeypatch: pytest.MonkeyPatch, clear_database_env: None
) -> None:
    monkeypatch.setenv("CINEMATCH_DATABASE_URL", CI_DATABASE_URL)
    settings = load_settings()
    assert settings.database_url == CI_DATABASE_URL
    assert "sslmode" not in settings.database_url
    assert settings.database_migration_url == CI_DATABASE_URL


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "db"])
def test_local_postgres_hosts_skip_ssl(
    monkeypatch: pytest.MonkeyPatch, clear_database_env: None, host: str
) -> None:
    url = f"postgresql+psycopg://cinematch:cinematch@{host}:5432/cinematch"
    monkeypatch.setenv("CINEMATCH_DATABASE_URL", url)
    settings = load_settings()
    assert settings.database_url == url
    assert "sslmode" not in (settings.database_migration_url or "")


def test_remote_postgres_url_requires_ssl(
    monkeypatch: pytest.MonkeyPatch, clear_database_env: None
) -> None:
    monkeypatch.setenv(
        "CINEMATCH_DATABASE_URL",
        "postgresql://user:pass@ep-example.eu-central-1.aws.neon.tech/cinematch?channel_binding=require",
    )
    settings = load_settings()
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert "sslmode=require" in settings.database_url
    assert "channel_binding" not in settings.database_url


def test_explicit_sslmode_is_preserved(
    monkeypatch: pytest.MonkeyPatch, clear_database_env: None
) -> None:
    url = "postgresql+psycopg://cinematch:cinematch@localhost:5432/cinematch?sslmode=disable"
    monkeypatch.setenv("CINEMATCH_DATABASE_URL", url)
    settings = load_settings()
    assert settings.database_url == url
    assert settings.database_url.count("sslmode") == 1


def test_constructor_sqlite_is_kept_when_neon_url_is_exported(
    monkeypatch: pytest.MonkeyPatch, clear_database_env: None, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@ep-example.eu-central-1.aws.neon.tech/neondb",
    )
    monkeypatch.setenv(
        "DATABASE_URL_UNPOOLED",
        "postgresql://user:pass@ep-example.eu-central-1.aws.neon.tech/neondb",
    )
    sqlite_url = f"sqlite:///{tmp_path / 'test.db'}"
    settings = load_settings(sqlite_url)
    assert settings.database_url == sqlite_url
    assert settings.database_migration_url == sqlite_url


def test_neon_pooled_url_is_used_when_cinematch_url_is_sqlite(
    monkeypatch: pytest.MonkeyPatch, clear_database_env: None
) -> None:
    monkeypatch.setenv("CINEMATCH_DATABASE_URL", "sqlite:///placeholder.db")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://user:pass@ep-example.eu-central-1.aws.neon.tech/neondb?channel_binding=require",
    )
    settings = load_settings()
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert "ep-example.eu-central-1.aws.neon.tech" in settings.database_url
    assert "sslmode=require" in settings.database_url
    assert "channel_binding" not in settings.database_url


def test_constructor_sqlite_is_kept_when_ci_exports_postgres(
    monkeypatch: pytest.MonkeyPatch, clear_database_env: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("CINEMATCH_DATABASE_URL", CI_DATABASE_URL)
    sqlite_url = f"sqlite:///{tmp_path / 'test.db'}"
    settings = load_settings(sqlite_url)
    assert settings.database_url == sqlite_url
    assert settings.database_migration_url == sqlite_url
