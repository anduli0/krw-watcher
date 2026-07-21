import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.database.models import Base


def _normalize_db_url(raw: str) -> tuple[str, dict]:
    """Accept the URL formats managed Postgres providers hand out (Railway/Render/Neon/
    Supabase) and return an async-driver URL + connect_args.

      postgres://… / postgresql://…  → postgresql+asyncpg://…
      ?sslmode=require / ?ssl=true    → connect_args={"ssl": True} (query stripped; asyncpg
                                        doesn't take libpq query params)
    SQLite (default, local dev) passes through unchanged.
    """
    connect_args: dict = {}
    ssl_required = ("sslmode=require" in raw) or ("ssl=true" in raw) or ("sslmode=verify" in raw)
    is_pg = raw.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://"))
    if is_pg:
        base = raw.split("?", 1)[0]
        for p in ("postgres://", "postgresql://"):
            if base.startswith(p):
                base = "postgresql+asyncpg://" + base[len(p):]
                break
        if ssl_required:
            connect_args["ssl"] = True
        return base, connect_args
    return raw, connect_args


DATABASE_URL, _CONNECT_ARGS = _normalize_db_url(
    os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./krw_watcher.db"))

_engine_kwargs: dict = {"echo": False, "future": True, "connect_args": _CONNECT_ARGS}
if DATABASE_URL.startswith("postgresql"):
    _engine_kwargs["pool_pre_ping"] = True   # recycle dead connections on managed PG

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
