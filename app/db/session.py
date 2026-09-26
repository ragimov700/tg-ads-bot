"""Async SQLAlchemy engine and advisory-lock helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

WORKER_LOCK_ID = 7_340_621_907


def create_engine(database_url: str | URL) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def advisory_lock(engine: AsyncEngine, lock_id: int = WORKER_LOCK_ID) -> AsyncIterator[bool]:
    async with engine.connect() as connection:
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
            )
        )
        try:
            yield acquired
        finally:
            if acquired:
                await _unlock(connection, lock_id)


async def _unlock(connection: AsyncConnection, lock_id: int) -> None:
    await connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
