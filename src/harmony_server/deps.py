"""Shared FastAPI dependencies (settings, db, device DB, current account)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel.ext.asyncio.session import AsyncSession

from harmony_server.auth.security import SCOPE_USER, decode_token
from harmony_server.config import Settings
from harmony_server.db import Account, Database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harmony_server.device_db.store import DeviceDB


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


async def get_session(db: Annotated[Database, Depends(get_db)]) -> AsyncIterator[AsyncSession]:
    async with db.session() as session:
        yield session


def get_device_db(request: Request) -> DeviceDB:
    store: DeviceDB = request.app.state.device_db
    return store


_bearer = HTTPBearer(auto_error=False)


async def current_account(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Account:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    account_id = decode_token(creds.credentials, settings, (SCOPE_USER,))
    if account_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    account = await session.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    return account


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AccountDep = Annotated[Account, Depends(current_account)]
