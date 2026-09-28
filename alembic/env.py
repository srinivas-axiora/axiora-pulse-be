"""
alembic/env.py
────────────────────────────────────────────────────────────────────────────────
Alembic environment configuration — async-aware.

Key design decisions:
  • DATABASE_URL is read directly from the .env file via python-dotenv,
    keeping a single source of truth.
  • Uses asyncio / SQLAlchemy async engine so migrations run with the same
    driver (asyncpg) as the main application.
  • `target_metadata` is set to Base.metadata so Alembic can auto-detect
    schema changes with `alembic revision --autogenerate`.
"""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Path fix — ensure `app` package is importable regardless of cwd ──────
import sys
from pathlib import Path

# Insert the backend directory (parent of this alembic/ dir) at the front of
# sys.path so that `from app.db.models import Base` always resolves correctly.
_backend_dir = str(Path(__file__).resolve().parents[1])
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# ── Load environment and models ──────────────────────────────────────────
load_dotenv()
from app.db.models import Base  # noqa: F401 – registers all ORM models

# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config

# Override the sqlalchemy.url with DATABASE_URL from .env
# (keeps .env as the single source of truth).
# Escape % signs — ConfigParser treats them as interpolation markers.
_database_url = os.getenv("DATABASE_URL", "")
config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None and config.get_main_option("skip_logging_config") != "true":
    fileConfig(config.config_file_name)

# Tell Alembic which metadata to inspect for autogenerate
target_metadata = Base.metadata


# ── Offline migration (generates SQL without a live DB connection) ─────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates a SQL script that can be applied manually.
    Useful for production deployments where direct DB access is restricted.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migration (applies migrations against a live DB) ───────────────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,          # Detect column type changes
        compare_server_default=True,# Detect server-default changes
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run pending migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,    # No pooling during migrations
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations (called by the Alembic CLI)."""
    asyncio.run(run_async_migrations())


# ── Dispatch ──────────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
