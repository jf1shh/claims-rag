"""Alembic migration environment.

Reads the target Postgres DSN from the POSTGRES_DSN env var (falling back to the
placeholder in alembic.ini). This is the same DSN a PostgresVectorStore consumes,
so `POSTGRES_DSN=... alembic upgrade head` provisions the schema the store expects.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_dsn = os.environ.get("POSTGRES_DSN") or config.get_main_option("sqlalchemy.url")
# POSTGRES_DSN is a plain postgresql:// URL (no driver). Force the psycopg v3
# dialect -- the only Postgres driver this project installs -- so SQLAlchemy
# never falls back to psycopg2.
if _dsn.startswith("postgresql://"):
    _dsn = _dsn.replace("postgresql://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", _dsn)

# The migrations are expressed as raw SQL (op.execute), so no declarative
# metadata is loaded; target_metadata stays None.
target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
