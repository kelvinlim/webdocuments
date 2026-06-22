"""Generate a consolidated "Bibliography & References Cited" document.

Zotero cannot keep a *live* bibliography in a document separate from its
citations — the bibliography field is computed from the citation fields in the
same document. For NIH submissions, References Cited is a *separate* ASSIST
attachment, so instead we read the CSL-JSON metadata Zotero already embeds in
each section document's citation fields, dedupe across documents, and render a
standalone bibliography with `pandoc --citeproc`.

This is read-only on the source documents (we never modify them — they keep
their live fields; OnlyOffice stays the source of truth). The generated doc is a
derived, flattened artifact.

A Zotero in-text citation is an OOXML complex field whose code lives in one or
more <w:instrText> runs:
    ADDIN ZOTERO_ITEM CSL_CITATION{ ...json... }
The JSON's citationItems[] each carry `itemData` (the work's CSL-JSON) and
`uris` (stable Zotero item URIs we dedupe on).
"""
import json
import logging
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import config, storage

log = logging.getLogger("webdocs")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Parts of a .docx that can contain citation fields (footnote/endnote styles put
# citations in the notes parts, not the body).
_DOCX_PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")

_ZOTERO_ITEM_MARKER = "ZOTERO_ITEM CSL_CITATION"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_field_codes(xml_bytes: bytes):
    """Yield each complex-field code string from one document part.

    Walks elements in document order, accumulating <w:instrText> text between a
    <w:fldChar begin> and its matching <w:fldChar end>. This reconstructs codes
    that Word/OnlyOffice split across multiple runs.
    """
    root = ET.fromstring(xml_bytes)
    depth = 0
    buf: list[str] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "fldChar":
            ftype = el.get(W + "fldCharType")
            if ftype == "begin":
                if depth == 0:
                    buf = []
                depth += 1
            elif ftype == "end":
                depth -= 1
                if depth <= 0:
                    depth = 0
                    if buf:
                        yield "".join(buf)
                        buf = []
        elif tag == "instrText" and depth >= 1 and el.text:
            buf.append(el.text)


def _items_from_field_code(code: str) -> list[dict]:
    """Extract CSL-JSON itemData objects from one Zotero citation field code.

    Returns [] for non-Zotero or non-CSL_CITATION fields (e.g. ZOTERO_BIBL).
    """
    if _ZOTERO_ITEM_MARKER not in code:
        return []
    brace = code.find("{")
    if brace == -1:
        return []
    try:
        # raw_decode parses one JSON object and ignores any trailing text.
        payload, _ = json.JSONDecoder().raw_decode(code[brace:])
    except json.JSONDecodeError:
        log.warning("references: skipping malformed Zotero field code")
        return []
    out = []
    for ci in payload.get("citationItems", []):
        item = ci.get("itemData")
        if isinstance(item, dict):
            # Carry the stable Zotero URI through for dedup.
            uris = ci.get("uris") or []
            if uris:
                item = {**item, "_uri": uris[0]}
            out.append(item)
    return out


def _dedup_key(item: dict) -> str:
    """Stable identity for a cited work, best-effort across libraries."""
    if item.get("_uri"):
        return f"uri:{item['_uri']}"
    if item.get("DOI"):
        return f"doi:{str(item['DOI']).strip().lower()}"
    pmid = _pmid(item)
    if pmid:
        return f"pmid:{pmid}"
    title = " ".join(str(item.get("title", "")).lower().split())
    year = ""
    issued = item.get("issued", {})
    parts = (issued.get("date-parts") or [[None]]) if isinstance(issued, dict) else [[None]]
    if parts and parts[0] and parts[0][0]:
        year = str(parts[0][0])
    author = ""
    auths = item.get("author") or []
    if auths and isinstance(auths[0], dict):
        author = str(auths[0].get("family", "")).lower()
    return f"tafa:{title}|{year}|{author}"


def _pmid(item: dict) -> str:
    """Pull a PMID out of the CSL note/extra field if present (Zotero stuffs
    PMID/PMCID there). Used only for dedup, not rendering."""
    blob = f"{item.get('note', '')}\n{item.get('extra', '')}"
    for line in blob.splitlines():
        line = line.strip()
        if line.upper().startswith("PMID:"):
            return line.split(":", 1)[1].strip()
    return ""


def extract_cited_items(doc_ids: list[str], owner: str) -> list[dict]:
    """Read every Zotero-cited work across the given (owner's) documents,
    deduplicated. Raises PermissionError if a doc isn't owned by `owner`."""
    seen: dict[str, dict] = {}
    for doc_id in doc_ids:
        meta = storage.get_meta(doc_id)
        path = storage.file_path(doc_id)
        if not meta or not path:
            log.warning("references: doc %s not found, skipping", doc_id)
            continue
        if meta.get("owner") != owner:
            raise PermissionError(f"{doc_id} not owned by {owner}")
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for part in _DOCX_PARTS:
                if part not in names:
                    continue
                for code in _iter_field_codes(zf.read(part)):
                    for item in _items_from_field_code(code):
                        seen.setdefault(_dedup_key(item), item)
    return list(seen.values())


def render_references_docx(items: list[dict]) -> bytes:
    """Render the cited works as a bibliography-only .docx via pandoc.

    Uses `nocite: '@*'` so every item appears in the bibliography with no
    in-text citation markers, and an empty body so the doc is the references
    list only.
    """
    csl = config.CSL_STYLE_PATH
    if not Path(csl).exists():
        raise FileNotFoundError(f"CSL style not found: {csl}")

    # Give each item a unique CSL id (pandoc requires it) and strip keys we
    # don't want in a bibliography: our private _uri helper, and the Zotero
    # short-title fields (some styles, e.g. AMA, wrongly render `title-short`
    # into the journal slot — and a references list uses full titles anyway).
    _DROP = {"_uri", "title-short", "shortTitle"}
    bib = []
    for i, item in enumerate(items, 1):
        clean = {k: v for k, v in item.items() if k not in _DROP}
        clean["id"] = f"ref-{i}"
        bib.append(clean)

    # nocite must be supplied via a YAML metadata block (and parsed as a
    # citation) — `--metadata nocite=@*` is treated as a plain string by
    # pandoc 3.x and emits nothing. The empty-bodied block yields a doc that is
    # just the title + the full bibliography (no in-text citations).
    body = (
        "---\n"
        "title: Bibliography and References Cited\n"
        "nocite: |\n"
        "  @*\n"
        "---\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        refs = tmp / "refs.json"
        out = tmp / "references.docx"
        refs.write_text(json.dumps(bib), encoding="utf-8")
        cmd = [
            "pandoc",
            "--from", "markdown",
            "--to", "docx",
            "--citeproc",
            "--bibliography", str(refs),
            "--csl", str(csl),
            "-o", str(out),
        ]
        proc = subprocess.run(
            cmd, input=body.encode("utf-8"), capture_output=True, timeout=60
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"pandoc failed ({proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace')[:500]}"
            )
        return out.read_bytes()
