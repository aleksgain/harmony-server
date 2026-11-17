"""``/api/auth/*`` - local account sign-up / sign-in issuing JWTs.

The same JWT doubles as the ``authToken`` the client writes into a hub during
provisioning (``setup.account?provision``), mirroring Logitech's UserAuthToken role.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from harmony_server.auth.models import AccountOut, Credentials, TokenResponse
from harmony_server.auth.security import (
    SCOPE_HUB,
    hash_password,
    issue_token,
    verify_password,
)
from harmony_server.db import Account
from harmony_server.deps import AccountDep, SessionDep, SettingsDep

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def authenticate(session: SessionDep, email: str, password: str) -> Account | None:
    account = (await session.exec(select(Account).where(Account.email == email.lower()))).first()
    if account is None or not await verify_password(password, account.hashed_password):
        return None
    return account


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    account = Account(email=body.email.lower(), hashed_password=await hash_password(body.password))
    session.add(account)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc
    await session.refresh(account)
    assert account.id is not None
    return _tokens(account.id, settings)


def _tokens(account_id: int, settings: SettingsDep) -> TokenResponse:
    """Collapse Logitech's 3-step chain (Logi ID -> Security.svc signin ->
    GetAuthenticatedUserAuthToken) into one call for the new client."""
    return TokenResponse(
        token=issue_token(account_id, settings),
        accountId=str(account_id),
        hubToken=issue_token(account_id, settings, scope=SCOPE_HUB),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: Credentials, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    account = await authenticate(session, body.email, body.password)
    if account is None or account.id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _tokens(account.id, settings)


@router.get("/me", response_model=AccountOut)
async def me(account: AccountDep) -> AccountOut:
    return AccountOut(accountId=str(account.id), email=account.email)
