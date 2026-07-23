from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import config
from bot.db.models import Base

engine = create_async_engine(f"sqlite+aiosqlite:///{config.database_path}")
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from bot.db.seed import seed_default_products

    async with async_session() as session:
        await seed_default_products(session)


@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
