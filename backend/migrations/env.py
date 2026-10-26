from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend import models  # noqa: F401
from backend.config import get_settings
from backend.db import Base

config = context.config
_settings = get_settings()
config.set_main_option(
    "sqlalchemy.url",
    _settings.database_migration_url or _settings.database_url,
)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    migration_url = _settings.database_migration_url or _settings.database_url
    connectable = create_engine(migration_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
