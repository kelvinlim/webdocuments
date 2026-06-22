# webdocuments — OnlyOffice + Zotero document editor (demo)

Self-hosted, browser-based `.docx` editor with live Zotero citations. A FastAPI
**connector** wraps a self-hosted **OnlyOffice Document Server** (which bundles the
Zotero plugin) and provides login, upload, document selection, editing, and download.

See [onlyoffice_zotero_plan.md](onlyoffice_zotero_plan.md) for the full plan and
[CLAUDE.md](CLAUDE.md) for orientation. **Citations round-trip live to MS Word** —
the make-or-break risk — is confirmed.

## Demo flow

Log in (username/password) → upload a `.docx` → pick it from the list → edit in
OnlyOffice with the Zotero plugin → download the result.

## Run it (local dev, Podman)

```bash
cp .env.example .env        # then edit the secrets
podman-compose up --build
```

- Connector: <http://localhost:8000>  (default login `demo` / `demo123`)
- Document Server: <http://localhost:8080>

The Document Server can take a minute to become healthy on first start.

## Run the connector alone (no container)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

You still need a reachable Document Server. Point `ONLYOFFICE_DS_PUBLIC_URL` at it,
and set `CONNECTOR_INTERNAL_URL` to a URL the DS can reach *back* on (if the DS runs
in a container and the connector on the host, that's `http://host.containers.internal:8000`).

## How the OnlyOffice integration works

| Endpoint | Caller | Purpose |
|---|---|---|
| `GET /editor/{id}` | browser | serves the page embedding `DocsAPI.DocEditor` with a JWT-signed config |
| `GET /files/{id}/download` | Document Server | loads the doc into the editor |
| `POST /callback/{id}` | Document Server | saves the edited doc back (status 2/3/6/7 + `url`) |

`document.url` and `callbackUrl` use `CONNECTOR_INTERNAL_URL` (server-to-server);
`api.js` is loaded from `ONLYOFFICE_DS_PUBLIC_URL` (browser). The shared
`ONLYOFFICE_JWT_SECRET` must match the Document Server's `JWT_SECRET`.

## Production serving

The host nginx serves everything under `/webdocs` (see [nginx/webdocs.conf](nginx/webdocs.conf)).
Set `ROOT_PATH=/webdocs` and `ONLYOFFICE_DS_PUBLIC_URL=/webdocs/ds`.

## Demo-only shortcuts (harden before real use)

- Plaintext passwords in `DEMO_USERS`; signed-cookie sessions, no CSRF.
- `GET /files/{id}/download` is unauthenticated (reachable only on the container network).
- No DB, locking, or versioning — one file per document on disk.
