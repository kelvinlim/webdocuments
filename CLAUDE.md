# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Early build. The FastAPI connector is scaffolded in [app/](app/); the editor engine is the off-the-shelf OnlyOffice Document Server run via Podman. [onlyoffice_zotero_plan.md](onlyoffice_zotero_plan.md) is the source of truth for decisions and build order — read it before changing direction.

## Commands

```bash
cp .env.example .env              # set secrets first
podman-compose up --build         # connector :8000 + Document Server :8080

# Connector alone (needs a reachable Document Server):
pip install -r requirements.txt
uvicorn app.main:app --reload
python3 -m py_compile app/*.py    # quick syntax check (no test suite yet)
```

Default demo login is `demo` / `demo123` (from `DEMO_USERS`). The Document Server's `JWT_SECRET` and the connector's `ONLYOFFICE_JWT_SECRET` must match.

## Connector layout ([app/](app/))

- [main.py](app/main.py) — FastAPI app + all routes. The OnlyOffice contract is three endpoints: `GET /editor/{id}` (serves the page embedding `DocsAPI.DocEditor` with a JWT-signed config), `GET /files/{id}/download` (DS fetches the doc to open), `POST /callback/{id}` (DS posts the edited doc back; status 2/3/6/7 carry a `url` to pull the result from).
- [onlyoffice.py](app/onlyoffice.py) — builds + JWT-signs the editor config. **Key URL split:** `document.url`/`callbackUrl` use `CONNECTOR_INTERNAL_URL` (Document Server → connector, server-to-server), while `api.js` loads from `ONLYOFFICE_DS_PUBLIC_URL` (browser → DS). Mixing these up is the usual cause of a blank editor or saves that never land.
- [storage.py](app/storage.py) — disk store (`data/docs/<id>/`), one file per doc. `document_key()` must change whenever content changes or OnlyOffice serves a stale cached version.
- [security.py](app/security.py) — demo plaintext auth + session cookie. [jwt_utils.py](app/jwt_utils.py) — HS256 sign/verify. [config.py](app/config.py) — env-driven settings.

## Goal

A self-hosted, browser-based document editor with **live, updatable Zotero citations**, for academic writing (the prelim thesis document). Privacy matters — the context is clinical/PHI-adjacent, so data stays self-hosted.

## This iteration is a capability demo

Prove the full loop works on our stack; do not over-build. The demo must support exactly: **log in (username/password) → upload a `.docx` → pick a doc from a list → edit it in OnlyOffice with Zotero citations → download it back out.** Multi-user sharing, a permissions matrix, robust/cloud storage, versioning UI, and OAuth are explicitly out of scope — keep auth and storage simple (seeded users, per-user folder on disk) and harden later. See section 0 of the plan.

## Locked decisions (do not relitigate)

- **Editor engine:** self-hosted OnlyOffice Docs (`onlyoffice/documentserver` Docker image), embedded in an iframe via `DocsAPI.DocEditor(elementId, config)`.
- **Citations:** live/updatable OOXML *fields*, not plain text — so restyling (APA ↔ Vancouver) and adding sources regenerates the bibliography automatically.
- **Zotero integration:** the stock `ONLYOFFICE/plugin-zotero` (ships with Document Server, field-based). Fork only if a concrete gap appears (custom CSL, custom UI).
- **Containers:** Podman / `podman-compose` (rootless, daemonless), not Docker — translate any upstream `docker run` / Docker Compose examples accordingly.
- **Serving:** nginx reverse proxy, mounted at the **`/webdocs`** sub-path on this server (not a domain root). OnlyOffice must be configured for sub-path serving so its assets/API resolve under `/webdocs`.
- **Auth (demo):** simple username/password, no OAuth/SSO. Just enough to gate access and identify the user.
- **Connector backend:** Python + FastAPI (same language as `make_approach.py`; PyJWT for signing; async suits the save-callback webhook). Crib from OnlyOffice's official Python connector example.

## Architecture

Three pieces — most engineering effort is the connector, **not** the editor:

1. **OnlyOffice Document Server** — the editor engine + bundled Zotero plugin. Off-the-shelf.
2. **Integration connector (the work we build)** — serves a document by a URL the server can fetch, implements the **callback URL** that persists saved versions, performs **JWT signing** (mandatory in current OnlyOffice versions), and backs storage with a file store (local disk for PoC → S3/DB later). Backend language (Python vs. Node) is still an open question.
3. **Zotero library** — reached over the Zotero Web API (API key), or local-library/offline mode in plugin v1.5+.

Build order is risk-first: (1) editor renders in a bare HTML page; (2) connector with doc-URL endpoint + callback + JWT; (3) Zotero citation round-trip survives Refresh/Synchronize on our deployment; (4) wire python-docx skeleton output as the starting document.

## Critical constraint: live citation fields

A live Zotero citation is **not text** — it's an OOXML field holding the rendered output *plus* hidden CSL-JSON metadata, backed by a document-preferences blob (style, field mode). That metadata is what makes Refresh/Synchronize work; flattening to text discards it (correct-looking but permanently frozen). **The rule:** any tool that doesn't speak Zotero's field protocol will ignore these fields at best and corrupt them at worst. `python-docx` is the main concrete instance — Python (`make_approach.py`) generates the **skeleton once** (headings, sections, figure placeholders, `{{CITE: Author Year}}` markers) and then **OnlyOffice is the source of truth**; never re-run Python over a doc that already has live fields. Programmatic post-processing happens *before* citations go in, or after a field→text flatten. See section 4 of the plan.

## Live round-trip to Microsoft Word — ✅ confirmed (was gating risk #1)

The core requirement — download the `.docx` and **resume editing in Word with citations still live** (editable/refreshable), not frozen — is **confirmed working** between OnlyOffice and Word desktop (bibliography round-trips in both directions, verified 2026-06-22). The premise holds; the project can proceed. Still TODO: record the exact OnlyOffice Zotero plugin + Word/Zotero versions, and re-run the section 4a test as a regression check whenever those versions change (compatibility has shifted release-to-release). Full rationale and test protocol remain in section 4a of the plan.

## When this moves forward

The plan's open questions (connector backend, file store, host-app auth, Zotero auth mode, required CSL styles, deployment/TLS) are unresolved — confirm choices with the user rather than assuming. See section 6 of the plan.
