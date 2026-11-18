"""Alembic environment. Works with the app's async drivers (asyncpg, aiosqlite).

URL precedence: `-x url=...` / config main option (used by tests) > DATABASE_URL via app settings.
"""
import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.core.config import get_settings
from app.core.db import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: emit SQL without connecting (for DBAs who review/apply it)."""
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                      render_as_batch=connection.dialect.name == "sqlite")  # SQLite can't ALTER freely
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
