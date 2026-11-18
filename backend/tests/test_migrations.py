"""Migrations must produce exactly the schema the models describe.

These are plain (sync) tests: Alembic's env.py calls asyncio.run() itself."""
import io
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.core.db import Base

BACKEND = Path(__file__).resolve().parent.parent


def _cfg(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "mig.db"
    return {"cfg": _cfg(f"sqlite+aiosqlite:///{path}"), "sync_url": f"sqlite:///{path}"}


def test_there_is_a_single_linear_history():
    script = ScriptDirectory(str(BACKEND / "alembic"))
    assert len(script.get_heads()) == 1  # two heads = two people branched; fix before deploying


def test_upgrade_creates_exactly_the_schema_the_models_declare(db):
    command.upgrade(db["cfg"], "head")
    engine = create_engine(db["sync_url"])
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"models and migrations disagree: {diff}"  # run `alembic revision --autogenerate`


def test_upgrade_creates_the_constraints_the_app_relies_on(db):
    command.upgrade(db["cfg"], "head")
    insp = inspect(create_engine(db["sync_url"]))
    assert {"users", "documents", "alembic_version"} <= set(insp.get_table_names())
    assert any(u["name"] == "uq_user_file_hash" for u in insp.get_unique_constraints("documents"))
    fk = insp.get_foreign_keys("documents")[0]
    assert fk["referred_table"] == "users" and fk["options"].get("ondelete") == "CASCADE"
    assert any(i["unique"] and i["column_names"] == ["email"] for i in insp.get_indexes("users"))


def test_downgrade_removes_everything_and_upgrade_is_repeatable(db):
    command.upgrade(db["cfg"], "head")
    command.downgrade(db["cfg"], "base")
    assert set(inspect(create_engine(db["sync_url"])).get_table_names()) <= {"alembic_version"}
    command.upgrade(db["cfg"], "head")  # idempotent round trip
    command.upgrade(db["cfg"], "head")  # already at head: no-op, no error


def test_postgres_ddl_can_be_generated_offline():
    """`alembic upgrade head --sql` against the production dialect (no database needed)."""
    cfg = _cfg("postgresql+asyncpg://u:p@localhost/db")
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "head", sql=True)
    sql = cfg.output_buffer.getvalue()
    assert "CREATE TABLE users" in sql and "CREATE TABLE documents" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "ON DELETE CASCADE" in sql and "CONSTRAINT uq_user_file_hash UNIQUE (user_id, file_hash)" in sql
    assert "CREATE UNIQUE INDEX ix_users_email" in sql


def test_app_tables_are_not_auto_created_when_disabled(tmp_path, monkeypatch):
    import asyncio

    from app.core import db as dbmod

    monkeypatch.setattr(dbmod.settings, "db_auto_create", False)
    path = tmp_path / "noauto.db"
    monkeypatch.setattr(dbmod, "engine", dbmod.create_async_engine(f"sqlite+aiosqlite:///{path}"))
    asyncio.run(dbmod.init_db())
    assert inspect(create_engine(f"sqlite:///{path}")).get_table_names() == []  # Alembic owns the schema
