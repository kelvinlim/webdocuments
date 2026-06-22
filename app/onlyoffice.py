"""Build the JWT-signed editor config handed to DocsAPI.DocEditor in the browser.

Two URLs in here are fetched by the Document Server (server-to-server), NOT by
the browser, so they use CONNECTOR_INTERNAL_URL:
  - document.url            where the DS downloads the doc to open it;
  - editorConfig.callbackUrl where the DS POSTs the edited doc back to save it.
"""
from . import config, jwt_utils, storage


def build_config(doc_id: str, user_id: str, user_name: str) -> dict:
    meta = storage.get_meta(doc_id)
    title = meta["title"] if meta else f"{doc_id}.docx"

    cfg = {
        "document": {
            "fileType": "docx",
            "key": storage.document_key(doc_id),
            "title": title,
            "url": f"{config.CONNECTOR_INTERNAL_URL}/files/{doc_id}/download",
        },
        "documentType": "word",
        "editorConfig": {
            "callbackUrl": f"{config.CONNECTOR_INTERNAL_URL}/callback/{doc_id}",
            "user": {"id": user_id, "name": user_name},
            "lang": "en",
            "customization": {"forcesave": True},
        },
    }
    # The token signs the whole config; the DS rejects the session if it fails.
    cfg["token"] = jwt_utils.sign(cfg)
    return cfg
