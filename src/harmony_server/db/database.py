"""SQLite persistence via SQLModel (async, aiosqlite)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Account(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=utcnow)


class Hub(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    account_id: int | None = Field(default=None, foreign_key="account.id", index=True)
    friendly_name: str = "Harmony Hub"
    #: Logitech's numeric remote id (== activeRemoteId == the 8088 ``hubId``).
    global_remote_id: int = Field(index=True, unique=True)
    #: Hub hardware identity (serial / uuid reported by the hub), if known.
    hub_uuid: str | None = Field(default=None, index=True)
    #: Token portion of the hub's ``cloudAuth`` blob. Stored in clear: the hub needs it back.
    cloud_token: str
    product_id: str = "Pimento"
    mode: int = 3
    hub_sw_version: str | None = None
    last_seen: datetime | None = None
    ip_address: str | None = None
    #: configVersion the hub last reported as successfully synced.
    synced_config_version: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class HarmonyConfig(SQLModel, table=True):
    """A compiled, immutable hub config bundle. Highest ``config_version`` wins."""

    __table_args__ = (UniqueConstraint("hub_id", "config_version"),)

    id: int | None = Field(default=None, primary_key=True)
    hub_id: int = Field(foreign_key="hub.id", index=True)
    config_version: int
    content_version: int = 1
    payload_json: str
    created_at: datetime = Field(default_factory=utcnow)


class Device(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("hub_id", "device_id"),)

    id: int | None = Field(default=None, primary_key=True)
    hub_id: int = Field(foreign_key="hub.id", index=True)
    device_id: str
    label: str
    type: str = "Television"
    manufacturer: str = ""
    model: str = ""
    global_device_id: int | None = None
    control_groups_json: str = "[]"
    extra_json: str = "{}"


class Activity(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("hub_id", "activity_id"),)

    id: int | None = Field(default=None, primary_key=True)
    hub_id: int = Field(foreign_key="hub.id", index=True)
    activity_id: str
    label: str
    type: str = "VirtualGeneric"
    devices_json: str = "[]"
    sequence_json: str = "[]"
    extra_json: str = "{}"


class Database:
    """Owns the async engine and hands out sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{path}", connect_args={"check_same_thread": False}
        )
        self._sessionmaker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self) -> None:
        from harmony_server.db.migrations import run_migrations

        await run_migrations(self.engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
