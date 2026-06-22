"""Demo auth: plaintext username/password against config.DEMO_USERS, with the
logged-in username kept in the signed session cookie.

DEMO ONLY. No password hashing, no lockout, no CSRF. Replace before any real use.
"""
from fastapi import Request

from . import config


def authenticate(username: str, password: str) -> bool:
    expected = config.DEMO_USERS.get(username)
    return expected is not None and expected == password


def get_user(request: Request) -> str | None:
    """Current logged-in username, or None."""
    return request.session.get("user")


def login(request: Request, username: str) -> None:
    request.session["user"] = username


def logout(request: Request) -> None:
    request.session.pop("user", None)
