# Browser-Based Editor with Zotero Citation Support (OnlyOffice)

> **Status:** Planning. This file documents the architecture and build plan before
> moving the work into a proper standalone project repository.
> **Goal:** A self-hosted, browser-based document editor with live, updatable Zotero
> citations — suitable for academic writing (e.g., the prelim thesis document).
> **This iteration is a capability demo**, not a production app: prove the full loop
> (auth → upload → edit with Zotero citations → download) works on our stack. Keep
> auth and storage deliberately simple; harden later.

---

## 1. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Editor engine | **Self-hosted OnlyOffice Docs** (`onlyoffice/documentserver`) | Full control; data stays private (relevant for clinical/PHI-adjacent context). |
| Citation model | **Live / updatable fields** | Restyle (APA ↔ Vancouver) and add sources with the whole bibliography regenerating — no manual re-insertion. |
| Zotero integration | **Official `ONLYOFFICE/plugin-zotero`** (stock first; fork only if needed) | Ships with Document Server; supports field-based citations natively. |
| Container runtime | **Podman / `podman-compose`** | Rootless, daemonless; matches this server's tooling. |
| Serving | **nginx reverse proxy**, mounted at the **`/webdocs`** path on this server | Single host; share the box with other apps via path-based routing. |
| Auth (demo) | **Simple username/password** (no OAuth/SSO) | Demo scope; just enough to gate access and identify the user. |
| Connector backend | **Python + FastAPI** | Same language as `make_approach.py` (one repo, shared `.docx` helpers); matches the Python/R stack; official OnlyOffice Python connector example; async fits the save-callback webhook; PyJWT for JWT signing. |

---

## 0. Demo scope (what this iteration must do)

The deliverable is a working end-to-end demo on our stack. Minimum user-facing flow:

1. **Log in** — username/password (no OAuth). A small fixed/seeded user set is fine.
2. **Upload** a `.docx` document into the app.
3. **Select** a document from a list to open in the editor.
4. **Edit** it in OnlyOffice, including inserting/refreshing Zotero citations.
5. **Output** — download the saved document back out.

Anything beyond this (multi-user sharing, permissions matrix, robust storage,
versioning UI, OAuth) is explicitly out of scope for the demo.

---

## 2. Key finding: the hard requirement is already supported

The official [ONLYOFFICE/plugin-zotero](https://github.com/ONLYOFFICE/plugin-zotero)
(v1.0.1+, refreshed Mar 2023 and Nov 2025 with offline/local-library mode) inserts
citations and bibliography **as fields, not plain text**:

- **Refresh** — re-renders all citation/bibliography fields in the document.
- **Synchronize** — pulls changes from the Zotero library and updates the doc
  (add sources, change CSL style → bibliography regenerates).
- **Convert field → text** — deliberate "flatten" for a portable copy that renders
  correctly in other editors.

This was the make-or-break risk (live fields vs. static text). It is handled natively,
so **no custom plugin is required** to start. Users authenticate with a Zotero Web API
key (or local library in v1.5+).

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                       │
│   ┌────────────────────────────────────────────────┐         │
│   │  Host web app page                                │         │
│   │   DocsAPI.DocEditor("placeholder", config)  ◄─────┼── JS API
│   │   └─ iframe: OnlyOffice editor                    │         │
│   │        └─ Zotero plugin (Plugins toolbar)         │         │
│   └────────────────────────────────────────────────┘         │
└───────────────┬───────────────────────────────┬───────────────┘
                │ loads doc by URL               │ Zotero Web API
                ▼                                 ▼  (API key)
┌─────────────────────────────┐          ┌──────────────────┐
│  OnlyOffice Document Server  │          │  Zotero library  │
│  (Docker, JWT-signed)        │          └──────────────────┘
└───────────────┬─────────────┘
                │ POST saved versions
                ▼  (callback URL)
┌─────────────────────────────┐
│  Integration connector (OURS)│  ← the real engineering work
│   - serve document by URL    │
│   - callback handler (save)  │
│   - JWT signing              │
│   - file store (disk/S3/DB)  │
└─────────────────────────────┘
```

### The three pieces
1. **OnlyOffice Docs (Document Server)** — editor engine in an iframe. Docker image
   `onlyoffice/documentserver`. The Zotero plugin ships with it.
2. **Integration connector (our code)** — the bulk of the work, *not* the editor:
   - embed via `DocsAPI.DocEditor(elementId, config)`;
   - serve the document by a URL the server can fetch;
   - implement the **callback URL** that persists saved versions;
   - enable **JWT signing** (mandatory in current versions);
   - back it with a file store (disk → S3/DB later).
3. **Zotero citations** — stock plugin, field-based; only fork the repo if a specific
   gap appears (custom CSL, custom UI).

---

## 4. The citation-field constraint (general form)

A live Zotero citation is **not text** — it is an OOXML **field** holding both the
rendered output (e.g. "(Smith, 2021)") and hidden **CSL-JSON metadata** describing which
Zotero items it points to. The document also carries a **document-preferences blob**
(active CSL style, storage/field mode). Together these are what let Refresh/Synchronize
re-render every field. Flatten to text ("Save as text") discards the metadata — the
result looks correct but can never be restyled or updated again.

**The rule:** any tool that does not speak Zotero's field protocol will, at best, ignore
these fields and, at worst, corrupt the metadata and break Refresh/Synchronize. This is
not specific to one tool — it governs every non-Zotero-aware touch of the document.

**python-docx (a concrete instance of the rule):**
- **Python generates the skeleton ONCE** — `make_approach.py` headings/sections/figure
  placeholders + `{{CITE: Author Year}}` markers → initial `.docx`.
- **All citation work happens inside OnlyOffice** after hand-off; the plugin's field
  insertion replaces the markers.
- **OnlyOffice is the source of truth post-handoff.** Do not re-run Python over a doc
  that already has live fields. Any programmatic post-processing happens *before*
  citations go in, or after flattening via field→text.

---

## 4a. GATING RISK #1 — live round-trip OnlyOffice ↔ Microsoft Word — ✅ CONFIRMED

> **RESOLVED (2026-06-22):** Round-trip of the Zotero bibliography between **OnlyOffice
> and Word desktop** is confirmed working — citations survive as live fields in both
> directions. The make-or-break premise holds. *(TODO: record the exact OnlyOffice
> Zotero plugin version and Word/Zotero versions used, so the claim is reproducible.)*
> The analysis below is kept as the rationale and the regression test to re-run on
> version bumps.

**The requirement:** download the `.docx` from our app and resume editing it in Word with
the Zotero citations still *live* (editable/refreshable), not frozen text.

**Why it's a risk, not a given.** Zotero's official Word/LibreOffice/Google-Docs plugins
share one integration protocol; in `.docx` the Word plugin encodes each citation as a
field code `ADDIN ZOTERO_ITEM CSL_CITATION { ...JSON... }` (+ `ZOTERO_BIBL` + the
doc-preferences blob). Cross-editor live editing works *only when every editor writes the
identical encoding.* Whether OnlyOffice's plugin writes byte-compatible Word field codes
**and** the doc-preferences blob is the open question. Signals:
- OnlyOffice docs are hedged: convert to text before opening "in other editors **which do
  not have the Zotero plugin**" — pointedly silent on editors that do (Word/LibreOffice).
- v1.5.0 (Nov 2025) and v1.0.6 (Jan 2026) both list **"improved compatibility with the
  Zotero plugin used in Microsoft Word"** — strong evidence they are converging on Word's
  format, and that it was *not* fully reliable before. "Improved" ≠ guaranteed.

**Three possible outcomes** when opening our OnlyOffice-authored `.docx` in Word + Zotero:
1. **Live** — Word recognizes fields + prefs; Add/Edit Citation, restyle, Refresh all work. (Goal.)
2. **Partial** — citations render but style/prefs don't carry; Word treats them as static or refresh misbehaves.
3. **Broken** — Word doesn't recognize them as Zotero fields; they appear as plain text / generic fields and editing breaks them.

The safe fallback (flatten to text) yields a **frozen** bibliography — which defeats this
requirement. So we must not depend on it; we must prove outcome (1).

**Test protocol (run EARLY — before building the connector):**
1. In OnlyOffice (pin the plugin version, ideally ≥ v1.5.0 / v1.0.6), insert 2–3
   citations + a bibliography, choose a CSL style, download the `.docx`.
2. Open in **Word** with the Zotero plugin, signed into the same library. Check in order:
   (a) citations render correctly; (b) **Add/Edit Citation** on an existing one opens the
   Zotero dialog with the right item pre-selected; (c) **Document Preferences** shows the
   style; (d) change style + **Refresh** re-renders everything correctly.
3. Reverse direction: Word-authored → open in OnlyOffice → Synchronize.
4. **Record the verified plugin version.** Claim "live round-trip verified with Word using
   plugin vX.Y.Z" — never a blanket claim, since this changes release-to-release.

Pass (2b)+(2c) → true live round-trip. Only (2a) passes → partial/static zone — reassess.

---

## 5. Build plan (proof-of-concept, ordered by risk)

0. ~~**Prove the Word round-trip FIRST (§4a).**~~ ✅ **Done (2026-06-22)** — confirmed on
   stock OnlyOffice + Word desktop. Premise holds; proceed. Re-run §4a as a regression
   check on plugin/version bumps.
1. **Editor loads.** `podman run onlyoffice/documentserver` (via `podman-compose`);
   confirm the editor renders in a bare HTML page via the JS API.
2. **Connector (FastAPI).** ✅ **Scaffolded + backend smoke-tested (2026-06-22).** `app/`
   has the three OnlyOffice endpoints (`/editor/{id}`, `/files/{id}/download`,
   `/callback/{id}`), JWT signing, demo auth, disk store, plus `podman-compose.yml` and
   `nginx/webdocs.conf`. Verified via `podman compose`: login → upload → list →
   JWT-signed editor config → both download paths → callback rejects missing JWT /
   accepts valid → **Document Server reaches the connector at `http://connector:8000`**.
   (Fixed a Starlette `TemplateResponse(request, name, ctx)` signature bug found here.)
3. **Zotero round-trip on our deployment** (⏳ next — needs a real browser). Open a doc in
   the running stack, enable the Zotero plugin, insert a citation, hit
   Refresh/Synchronize — confirm the field round-trip survives on *our* stack, then
   re-run the §4a Word check end-to-end (download → Word → refresh). This is the one step
   the headless backend smoke test (step 2) can't cover.
4. **Demo flow (§0).** Login (username/password) → upload → select-from-list → edit →
   download.
5. **Wire in python-docx output** as the starting document (skeleton only).

---

## 6. Open questions / TODO when moving to the project repo

- [x] Backend language for the connector — **Python + FastAPI** (see Decisions table).
- [ ] File store: local disk for the demo → S3/DB for real use. (Demo: a per-user folder on disk is enough.)
- [x] Auth model for the host app — **simple username/password** for the demo (no OAuth). Who-can-open-which-doc rules deferred.
- [ ] Zotero auth: Web API key vs. local-library/offline mode.
- [ ] Where `make_approach.py` and the skeleton-generation step live in the new repo.
- [ ] CSL style(s) required (APA / NIH / Vancouver?).
- [ ] TLS termination for `/webdocs` (nginx) — needed for JWT and secure cookies.
- [ ] OnlyOffice base-path config so the editor's assets/API resolve under `/webdocs` (sub-path serving, not a domain root).

---

## 7. References

- [ONLYOFFICE/plugin-zotero (GitHub)](https://github.com/ONLYOFFICE/plugin-zotero)
- [Meet updated Zotero plugin for ONLYOFFICE editors (2023)](https://www.onlyoffice.com/blog/2023/03/meet-updated-zotero-plugin)
- [Updated Zotero plugin with offline mode (Nov 2025)](https://www.onlyoffice.com/blog/2025/11/updated-zotero-plugin-for-onlyoffice-150)
- [Mendeley, Zotero: Inserting references (Help Center)](https://helpcenter.onlyoffice.com/docs/userguides/plugins/InsertReferences.aspx)
- [Zotero plugin sample (OnlyOffice API docs)](https://api.onlyoffice.com/docs/plugin-and-macros/samples/plugin-samples/zotero/)
- [OnlyOffice integration / connector docs](https://api.onlyoffice.com/docs/)
