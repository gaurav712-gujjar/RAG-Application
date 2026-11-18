from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


_kwargs = (
    {"poolclass": NullPool}
    if settings.database_url.startswith("sqlite")
    else {"pool_pre_ping": True}
)
engine = create_async_engine(settings.database_url, **_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create tables for dev/tests. In production set DB_AUTO_CREATE=false and use Alembic."""
    import app.models  # noqa: F401  (registers tables on Base)

    if not settings.db_auto_create:
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
