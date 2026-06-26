"""Configuration, read from environment variables.

Kept deliberately simple for the demo — no pydantic-settings, just os.environ
with sane local-dev defaults. See .env.example for what each value means.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Where uploaded + edited documents are stored on disk.
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BASE_DIR / "data"))
DOCS_DIR = STORAGE_DIR / "docs"

# Session cookie signing key.
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")

# Sub-path the app is served under by the host nginx, e.g. "/webdocs".
# Empty for local dev (hitting the connector directly). Passed to FastAPI
# as root_path so generated URLs carry the prefix.
ROOT_PATH = os.environ.get("ROOT_PATH", "").rstrip("/")

# Shared secret for OnlyOffice JWT (must match the documentserver's JWT_SECRET).
ONLYOFFICE_JWT_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "dev-onlyoffice-secret")

# Browser-facing base URL of the Document Server (serves api.js).
ONLYOFFICE_DS_PUBLIC_URL = os.environ.get(
    "ONLYOFFICE_DS_PUBLIC_URL", "http://localhost:8080"
).rstrip("/")

# Base URL the Document Server uses to reach THIS connector (server-to-server:
# document download + save callback). On the compose network this is the
# connector's service name; in local dev it's localhost.
CONNECTOR_INTERNAL_URL = os.environ.get(
    "CONNECTOR_INTERNAL_URL", "http://localhost:8000"
).rstrip("/")

# URL the browser loads the editor API from.
DS_API_JS = f"{ONLYOFFICE_DS_PUBLIC_URL}/web-apps/apps/api/documents/api.js"

# CSL style used to render the consolidated "References Cited" document.
# Default is AMA (numbered biomedical style, a common NIH fit). Override with
# CSL_STYLE_PATH: a bare filename (e.g. "vancouver.csl") is resolved against
# app/styles/; a value containing "/" is treated as an absolute/explicit path.
STYLES_DIR = Path(__file__).parent / "styles"
_csl = os.environ.get("CSL_STYLE_PATH", "").strip()
if not _csl:
    CSL_STYLE_PATH = STYLES_DIR / "american-medical-association.csl"
elif "/" in _csl:
    CSL_STYLE_PATH = Path(_csl)
else:
    CSL_STYLE_PATH = STYLES_DIR / _csl

# Pandoc reference doc that sets the generated bibliography's page/formatting
# (0.5" margins, Arial 11). Built once and shipped in app/styles/.
REFERENCE_DOCX = STYLES_DIR / "reference.docx"


def _parse_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, _, pw = pair.partition(":")
        users[name.strip()] = pw
    return users


# Demo users: plaintext "user:pass" pairs. NOT for production.
DEMO_USERS = _parse_users(os.environ.get("DEMO_USERS", "demo:demo123"))
