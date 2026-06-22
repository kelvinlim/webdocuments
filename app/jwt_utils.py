"""JWT helpers for the OnlyOffice integration (HS256, shared secret).

Used in two places:
  - signing the editor config we hand to the browser (config["token"]);
  - verifying the save callback the Document Server POSTs back to us.
"""
import jwt

from . import config


def sign(payload: dict) -> str:
    return jwt.encode(payload, config.ONLYOFFICE_JWT_SECRET, algorithm="HS256")


def verify(token: str) -> dict:
    """Decode + verify a token. Raises jwt.PyJWTError on tampering/expiry."""
    return jwt.decode(token, config.ONLYOFFICE_JWT_SECRET, algorithms=["HS256"])
