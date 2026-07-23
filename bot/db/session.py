from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import config
from bot.db.models import Base

engine = create_async_engine(f"sqlite+aiosqlite:///{config.database_path}")
async_session = async_sessionmaker(engine, expire_on_commit=False)

# create_all only creates tables that don't exist yet — it never adds new
# columns to a table that's already there. Since this bot has been running
# in production and picking up model changes across deploys, every column
# added after the very first deploy needs an explicit ALTER TABLE here, or
# the live SQLite file falls behind the models and every query touching it
# fails with "no such column".
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "orders": [
        ("payment_proof_hash", "VARCHAR(64)"),
        ("paid_with_referral_balance", "BOOLEAN DEFAULT 0"),
    ],
    "users": [
        ("accepted_terms_at", "DATETIME"),
        ("referred_by", "BIGINT"),
        ("referral_balance", "FLOAT DEFAULT 0.0"),
    ],
    "products": [
        # SQLAlchemy's Enum type stores the member NAME by default (e.g.
        # "DIAMONDS"), not its .value ("diamonds") — the default here must
        # match that or every pre-existing row fails to deserialize.
        ("category", "VARCHAR(16) DEFAULT 'DIAMONDS'"),
    ],
}


async def _apply_column_migrations(conn) -> None:
    for table, columns in _COLUMN_MIGRATIONS.items():
        result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        existing = {row[1] for row in result.fetchall()}
        for name, column_def in columns:
            if name not in existing:
                await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {column_def}")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_column_migrations(conn)

    from bot.db.seed import seed_default_products

    async with async_session() as session:
        await seed_default_products(session)


@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
