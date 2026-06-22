"""Disk-backed document store for the demo.

Layout (under STORAGE_DIR):
    docs/<doc_id>/document.docx   the file itself (source of truth)
    docs/<doc_id>/meta.json       {id, owner, title, created, modified}

Per the plan, OnlyOffice is the source of truth once a doc has live citation
fields — so we only ever overwrite document.docx wholesale with what the
Document Server hands back; we never parse or rewrite its contents here.

Demo simplifications: no DB, no locking, no versioning. One file per document.
"""
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from . import config


def init_storage() -> None:
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)


def _doc_dir(doc_id: str) -> Path:
    return config.DOCS_DIR / doc_id


def _file_path(doc_id: str) -> Path:
    return _doc_dir(doc_id) / "document.docx"


def _meta_path(doc_id: str) -> Path:
    return _doc_dir(doc_id) / "meta.json"


def _write_meta(meta: dict) -> None:
    _meta_path(meta["id"]).write_text(json.dumps(meta, indent=2))


def get_meta(doc_id: str) -> Optional[dict]:
    p = _meta_path(doc_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def create_document(owner: str, title: str, src: BinaryIO) -> str:
    doc_id = uuid.uuid4().hex
    _doc_dir(doc_id).mkdir(parents=True)
    with open(_file_path(doc_id), "wb") as f:
        shutil.copyfileobj(src, f)
    now = time.time()
    _write_meta(
        {"id": doc_id, "owner": owner, "title": title, "created": now, "modified": now}
    )
    return doc_id


def list_documents(owner: str) -> list[dict]:
    if not config.DOCS_DIR.exists():
        return []
    out = [
        meta
        for d in config.DOCS_DIR.iterdir()
        if d.is_dir() and (meta := get_meta(d.name)) and meta.get("owner") == owner
    ]
    out.sort(key=lambda m: m.get("modified", 0), reverse=True)
    return out


def file_path(doc_id: str) -> Optional[Path]:
    p = _file_path(doc_id)
    return p if p.exists() else None


def save_bytes(doc_id: str, data: bytes) -> None:
    """Overwrite the stored document with bytes pulled from the Document Server."""
    with open(_file_path(doc_id), "wb") as f:
        f.write(data)
    meta = get_meta(doc_id)
    if meta:
        meta["modified"] = time.time()
        _write_meta(meta)


def document_key(doc_id: str) -> str:
    """OnlyOffice document key: identifies a document *version*. Must change
    whenever the content changes, be unique, and stay within 128 chars."""
    meta = get_meta(doc_id)
    mtime = int(meta.get("modified", 0)) if meta else 0
    return f"{doc_id}-{mtime}"[:128]
