from __future__ import annotations

from pydantic import BaseModel, Field


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=8, max_length=256)


class TokenResponse(BaseModel):
    token: str
    accountId: str
    tokenType: str = "bearer"
    #: Ready to write into a hub as ``provisionInfo.authToken`` (step-3 equivalent).
    hubToken: str


class AccountOut(BaseModel):
    accountId: str
    email: str
