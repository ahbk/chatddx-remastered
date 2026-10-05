import hashlib
import re
import secrets
from enum import StrEnum
from typing import ClassVar

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel, ConfigDict, field_validator

# Lowercase, as Keycloak stores usernames, so logins carry over unchanged.
# src/chatddx/store/migrations/0008-t2-identity-checks.sql repeats this pattern and the role names.
LOGIN = re.compile(r"^[a-z0-9][a-z0-9._@-]*$")


class Role(StrEnum):
    ADMIN = "admin"
    CLINICIAN = "clinician"
    DEVELOPER = "developer"
    OPS = "ops"
    RESEARCHER = "researcher"


class Person(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: int
    login: str
    name: str
    roles: frozenset[Role] = frozenset()
    active: bool = True

    @field_validator("login")
    @classmethod
    def _login(cls, login: str) -> str:
        if not LOGIN.match(login):
            raise ValueError(f"invalid login {login!r}")
        return login


_hasher = PasswordHasher()
# Verified against when a login doesn't exist, so that an unknown login takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe())


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(hash or _DUMMY_HASH, password) and hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(hash: str) -> bool:
    return _hasher.check_needs_rehash(hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
