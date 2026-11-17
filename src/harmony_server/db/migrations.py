"""Minimal forward-only migrations keyed on a ``schema_version`` table.

Add a new ``(version, fn)`` pair to ``MIGRATIONS`` for every schema change; never edit an
applied one. No Alembic: the schema is small and this keeps the image lean.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlmodel import SQLModel

log = logging.getLogger(__name__)

Migration = Callable[[AsyncConnection], Awaitable[None]]


async def _v1_initial(conn: AsyncConnection) -> None:
    from harmony_server.db import database  # noqa: F401  (registers tables)

    await conn.run_sync(SQLModel.metadata.create_all)


MIGRATIONS: list[tuple[int, Migration]] = [
    (1, _v1_initial),
]


async def current_version(conn: AsyncConnection) -> int:
    await conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"))
    row = (await conn.execute(text("SELECT MAX(version) FROM schema_version"))).scalar()
    return int(row or 0)


async def run_migrations(engine: AsyncEngine) -> int:
    async with engine.begin() as conn:
        version = await current_version(conn)
        for target, fn in MIGRATIONS:
            if target > version:
                log.info("Applying schema migration v%d", target)
                await fn(conn)
                await conn.execute(
                    text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": target}
                )
                version = target
    return version
