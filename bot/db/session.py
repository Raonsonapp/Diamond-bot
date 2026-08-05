import ssl
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import config
from bot.db.models import Base, OrderStatus, ProductCategory

if config.database_url:
    # Supabase's URI comes with libpq-only query params (sslmode,
    # channel_binding) that asyncpg's connect() rejects outright as
    # unexpected keyword arguments — strip them and request TLS the
    # asyncpg way via connect_args instead. Also disable the prepared-
    # statement cache: Supabase's pooler (and pgbouncer poolers generally)
    # don't support asyncpg's server-side prepared statements across
    # pooled connections, which otherwise fails randomly under load with
    # "prepared statement ... already exists".
    _pg_url = make_url(config.database_url).difference_update_query(["sslmode", "channel_binding"])
    # Plain ssl=True does full certificate-chain verification, which fails
    # against Supabase's pooler cert chain in Python's default trust store
    # ("self-signed certificate in certificate chain"). The connection is
    # still fully encrypted; only strict chain-of-trust checking is
    # relaxed here — Supabase's own security model for this endpoint is
    # the username/password, not client-side PKI verification.
    _ssl_context = ssl.create_default_context()
    _ssl_context.check_hostname = False
    _ssl_context.verify_mode = ssl.CERT_NONE
    engine = create_async_engine(_pg_url, connect_args={"ssl": _ssl_context, "statement_cache_size": 0})
else:
    engine = create_async_engine(f"sqlite+aiosqlite:///{config.database_path}")
async_session = async_sessionmaker(engine, expire_on_commit=False)

# create_all only creates tables that don't exist yet — it never adds new
# columns to a table that's already there. That's normally just a concern
# for columns added after the very first deploy, but a table can also end
# up with a subset of even its ORIGINAL columns — e.g. several overlapping
# deploy attempts racing create_all() against the same fresh Postgres
# database left a `users` table missing `username` outright. So every
# column on every model is listed here, not just the ones added later, and
# this doubles as living documentation of the full schema.
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "bot_users": [
        ("username", "VARCHAR(64)"),
        ("full_name", "VARCHAR(128)"),
        ("created_at", "DATETIME"),
        ("accepted_terms_at", "DATETIME"),
        ("referred_by", "BIGINT"),
        ("referral_balance", "FLOAT DEFAULT 0.0"),
    ],
    "bot_products": [
        ("name", "VARCHAR(64)"),
        # SQLAlchemy's Enum type stores the member NAME by default (e.g.
        # "DIAMONDS"), not its .value ("diamonds") — the default here must
        # match that or every pre-existing row fails to deserialize.
        ("category", "VARCHAR(16) DEFAULT 'DIAMONDS'"),
        ("diamonds", "INTEGER"),
        ("bonus_diamonds", "INTEGER DEFAULT 0"),
        ("price_somoni", "FLOAT"),
        ("cost_somoni", "FLOAT DEFAULT 0.0"),
        ("is_active", "BOOLEAN DEFAULT 1"),
        ("fzr_category_id", "VARCHAR(64)"),
        ("fzr_offer_id", "VARCHAR(64)"),
        ("telegram_kind", "VARCHAR(16)"),
    ],
    "bot_orders": [
        ("user_id", "BIGINT"),
        ("product_id", "INTEGER"),
        ("ff_player_id", "VARCHAR(32)"),
        ("amount_somoni", "FLOAT"),
        ("paid_with_referral_balance", "BOOLEAN DEFAULT 0"),
        # Same Enum-stores-.name-not-.value gotcha as products.category.
        ("status", "VARCHAR(16) DEFAULT 'AWAITING_PAYMENT'"),
        ("payment_provider", "VARCHAR(32) DEFAULT 'manual'"),
        ("payment_reference", "VARCHAR(128)"),
        ("admin_note", "VARCHAR(256)"),
        ("payment_proof_hash", "VARCHAR(64)"),
        ("proof_submitted_at", "DATETIME"),
        ("progress_message_id", "INTEGER"),
        ("cart_group_id", "VARCHAR(32)"),
        ("created_at", "DATETIME"),
        ("updated_at", "DATETIME"),
    ],
}


# The column defs above are written in SQLite's loose syntax; Postgres
# rejects a couple of them outright (no DATETIME type, no bare `0`/`1` as
# a BOOLEAN default), so translate just those on non-SQLite dialects.
_POSTGRES_COLDEF_OVERRIDES = {
    "DATETIME": "TIMESTAMPTZ",
    "BOOLEAN DEFAULT 0": "BOOLEAN DEFAULT FALSE",
    "BOOLEAN DEFAULT 1": "BOOLEAN DEFAULT TRUE",
}


async def _apply_column_migrations(conn) -> None:
    is_sqlite = conn.engine.dialect.name == "sqlite"
    for table, columns in _COLUMN_MIGRATIONS.items():
        if is_sqlite:
            result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
            existing = {row[1] for row in result.fetchall()}
        else:
            result = await conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": table},
            )
            existing = {row[0] for row in result.fetchall()}

        for name, column_def in columns:
            if name in existing:
                continue
            if not is_sqlite:
                column_def = _POSTGRES_COLDEF_OVERRIDES.get(column_def, column_def)
            await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {column_def}")


_ENUM_MIGRATIONS = {
    "productcategory": ProductCategory,
    "orderstatus": OrderStatus,
}


async def _apply_enum_migrations() -> None:
    """create_all() only creates a Postgres native ENUM TYPE if it doesn't
    exist yet — it never adds new members to a type that's already there.
    So a member added to ProductCategory/OrderStatus after the type was
    first created (e.g. FF_INDONESIA, added long after this table already
    existed live) silently doesn't exist in the database at all: any INSERT
    using it fails with "invalid input value for enum", which surfaces as
    the whole admin command just not responding (the exception happens
    inside session.commit(), after the handler's own try/except has
    nothing left to catch). SQLite has no native enum type (Enum() falls
    back to a plain VARCHAR there), so this only matters on Postgres.

    ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
    Postgres < 12, so each statement gets its own autocommit connection
    rather than sharing engine.begin() with the rest of init_db()."""
    if engine.dialect.name == "sqlite":
        return

    for type_name, enum_cls in _ENUM_MIGRATIONS.items():
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT enumlabel FROM pg_enum "
                    "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                    "WHERE pg_type.typname = :t"
                ),
                {"t": type_name},
            )
            existing = {row[0] for row in result.fetchall()}

        if not existing:
            # Type doesn't exist at all yet (brand new database) —
            # create_all() below creates it fresh with every current
            # member, nothing to migrate.
            continue

        for member in enum_cls:
            if member.name in existing:
                continue
            async with engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.exec_driver_sql(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{member.name}'")


async def init_db() -> None:
    await _apply_enum_migrations()

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
