from contextlib import asynccontextmanager

import aiomysql

from ..config import settings

_pool: aiomysql.Pool | None = None


async def _get_pool() -> aiomysql.Pool:
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            db=settings.DB_NAME,
            cursorclass=aiomysql.DictCursor,
            minsize=1,
            maxsize=10,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


@asynccontextmanager
async def get_db_conn():
    pool = await _get_pool()
    async with pool.acquire() as conn:
        yield conn
