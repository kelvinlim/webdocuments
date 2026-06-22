"""FastAPI integration connector for the OnlyOffice + Zotero demo.

Flow: login (username/password) -> upload .docx -> pick from list -> edit in
OnlyOffice (with the Zotero plugin) -> download the result.

The OnlyOffice integration contract lives in three endpoints:
  GET  /files/{id}/download   the Document Server fetches the doc to open it
  POST /callback/{id}         the Document Server saves the edited doc back
  GET  /editor/{id}           serves the page that embeds DocsAPI.DocEditor
"""
import io
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import jwt
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, jwt_utils, onlyoffice, references, security, storage

log = logging.getLogger("webdocs")

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_storage()
    yield


app = FastAPI(title="webdocs connector", root_path=config.ROOT_PATH, lifespan=lifespan)
# https_only marks the session cookie Secure; the demo is served only over
# HTTPS (host nginx terminates TLS), so the browser will still send it.
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, https_only=True)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# --- small helpers -----------------------------------------------------------

def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.update(root_path=config.ROOT_PATH)
    # Starlette's current signature is (request, name, context); request must
    # be the first positional arg, not stuffed into the context dict.
    return templates.TemplateResponse(request, name, ctx)


def _redirect(path: str) -> RedirectResponse:
    # Browser-facing, so carry the sub-path prefix.
    return RedirectResponse(url=f"{config.ROOT_PATH}{path}", status_code=303)


# --- auth ---------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _redirect("/documents" if security.get_user(request) else "/login")


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if security.get_user(request):
        return _redirect("/documents")
    return _render(request, "login.html", error=None)


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not security.authenticate(username, password):
        return _render(request, "login.html", error="Invalid username or password.")
    security.login(request, username)
    return _redirect("/documents")


@app.post("/logout")
def logout(request: Request):
    security.logout(request)
    return _redirect("/login")


# --- document list + upload + user download -----------------------------------

@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request):
    user = security.get_user(request)
    if not user:
        return _redirect("/login")
    return _render(request, "documents.html", user=user, docs=storage.list_documents(user))


@app.post("/documents/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    user = security.get_user(request)
    if not user:
        return _redirect("/login")
    if not (file.filename or "").lower().endswith(".docx"):
        return _render(
            request,
            "documents.html",
            user=user,
            docs=storage.list_documents(user),
            error="Only .docx files are supported.",
        )
    storage.create_document(user, file.filename, file.file)
    return _redirect("/documents")


@app.post("/references/generate")
def generate_references(request: Request, doc_ids: list[str] = Form(default=[])):
    """Build a consolidated 'Bibliography & References Cited' .docx from the
    Zotero citations across the selected documents, and store it as a new doc."""
    user = security.get_user(request)
    if not user:
        return _redirect("/login")

    def _err(msg: str):
        return _render(
            request, "documents.html", user=user,
            docs=storage.list_documents(user), error=msg,
        )

    if len(doc_ids) < 2:
        return _err("Select at least two documents to consolidate references from.")
    try:
        items = references.extract_cited_items(doc_ids, user)
    except PermissionError:
        return Response("Not found", status_code=404)
    except Exception:
        log.exception("references: extraction failed")
        return _err("Could not read citations from the selected documents.")

    if not items:
        return _err("No Zotero citations found in the selected documents.")

    try:
        data = references.render_references_docx(items)
    except Exception:
        log.exception("references: rendering failed")
        return _err("Could not render the references document. Is pandoc installed?")

    storage.create_document(user, "Bibliography & References Cited.docx", io.BytesIO(data))
    return _redirect("/documents")


@app.get("/documents/{doc_id}/download")
def user_download(request: Request, doc_id: str):
    """User-facing 'output the document' — owner only."""
    user = security.get_user(request)
    if not user:
        return _redirect("/login")
    meta = storage.get_meta(doc_id)
    path = storage.file_path(doc_id)
    if not meta or not path or meta.get("owner") != user:
        return Response("Not found", status_code=404)
    return FileResponse(path, media_type=DOCX_MEDIA, filename=meta["title"])


# --- editor -------------------------------------------------------------------

@app.get("/editor/{doc_id}", response_class=HTMLResponse)
def editor(request: Request, doc_id: str):
    user = security.get_user(request)
    if not user:
        return _redirect("/login")
    meta = storage.get_meta(doc_id)
    if not meta or meta.get("owner") != user:
        return Response("Not found", status_code=404)
    cfg = onlyoffice.build_config(doc_id, user_id=user, user_name=user)
    return _render(
        request,
        "editor.html",
        title=meta["title"],
        ds_api_js=config.DS_API_JS,
        config_json=json.dumps(cfg),
    )


# --- OnlyOffice server-to-server contract -------------------------------------

@app.get("/files/{doc_id}/download")
def ds_download(doc_id: str):
    """Called by the Document Server to load a document for editing.

    DEMO: open endpoint. The internal URL is only reachable from the DS on the
    container network. Harden later (e.g. signed one-time token in the URL).
    """
    path = storage.file_path(doc_id)
    if not path:
        return Response("Not found", status_code=404)
    return FileResponse(path, media_type=DOCX_MEDIA)


@app.post("/callback/{doc_id}")
async def ds_callback(doc_id: str, request: Request):
    """Save callback from the Document Server.

    With JWT enabled the body carries a `token` (JWT of the body); verify it and
    treat the decoded payload as authoritative. status 2/3 = ready to save,
    6/7 = force-save while editing. A `url` to fetch the edited file is present
    for 2/3/6/7. Must return {"error": 0} on success.
    """
    body = await request.json()

    token = body.get("token")
    if token:
        try:
            body = jwt_utils.verify(token)
        except jwt.PyJWTError:
            log.warning("callback %s: bad JWT", doc_id)
            return JSONResponse({"error": 1})
    elif config.ONLYOFFICE_JWT_SECRET:
        # JWT is configured but the DS sent no token — reject.
        log.warning("callback %s: missing JWT", doc_id)
        return JSONResponse({"error": 1})

    status = body.get("status")
    if status in (2, 3, 6, 7):
        url = body.get("url")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            storage.save_bytes(doc_id, resp.content)
            log.info("callback %s: saved (status=%s)", doc_id, status)

    return JSONResponse({"error": 0})
