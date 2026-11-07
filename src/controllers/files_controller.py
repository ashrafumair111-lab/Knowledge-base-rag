"""Session files + passages (sidebar aur file viewer) controllers."""

import hashlib

from fastapi import HTTPException
from qdrant_client import models

from src import config
from src.db import qdrant as qdrant_db

MAX_SCROLL_PAGES = 200  # 256 chunks/page -> ~51k chunks per session, uske baad band


def list_session_files(sid: str) -> list[dict]:
    """Is session ki files + har file ke chunks (sirf source field padhi jati hai, text/vectors nahi)."""
    session_filter = models.Filter(
        must=[models.FieldCondition(key="metadata.session_id", match=models.MatchValue(value=sid))]
    )
    counts: dict[str, int] = {}
    offset = None
    for _ in range(MAX_SCROLL_PAGES):
        records, offset = qdrant_db.with_heal(
            lambda: qdrant_db.qdrant.scroll(
                collection_name=config.COLLECTION_NAME,
                scroll_filter=session_filter,
                limit=256,
                offset=offset,
                with_payload=models.PayloadSelectorInclude(include=["metadata.source"]),
                with_vectors=False,
            )
        )
        for record in records:
            source = (record.payload or {}).get("metadata", {}).get("source")
            if source:
                counts[source] = counts.get(source, 0) + 1
        if offset is None:
            break
    return [{"source": name, "chunks": counts[name]} for name in sorted(counts)]


def session_files(sid: str) -> dict:
    """Refresh ke baad sidebar list wapas banane ke liye.

    "session" = sid ka hash (sid khud nahi): browser isse chat history ki localStorage key banata hai.
    """
    chat_key = hashlib.sha256(f"chat::{sid}".encode()).hexdigest()[:16]
    return {"files": list_session_files(sid), "session": chat_key}


def session_passages(source: str, sid: str) -> dict:
    """Is session ki ek file ke passages (viewer ke liye), padhne ke order me."""
    file_filter = models.Filter(
        must=[
            models.FieldCondition(key="metadata.session_id", match=models.MatchValue(value=sid)),
            models.FieldCondition(key="metadata.source", match=models.MatchValue(value=source)),
        ]
    )
    rows: list[tuple[int, str]] = []
    offset = None
    for _ in range(MAX_SCROLL_PAGES):
        records, offset = qdrant_db.with_heal(
            lambda: qdrant_db.qdrant.scroll(
                collection_name=config.COLLECTION_NAME,
                scroll_filter=file_filter,
                limit=256,
                offset=offset,
                with_payload=models.PayloadSelectorInclude(include=["page_content", "metadata.chunk"]),
                with_vectors=False,
            )
        )
        for record in records:
            payload = record.payload or {}
            # purane chunks (chunk number ke bina) end me, apne aap ke order me
            rows.append((payload.get("metadata", {}).get("chunk", 10**9), payload.get("page_content", "")))
        if offset is None:
            break
    if not rows:
        raise HTTPException(status_code=404, detail="That file is not in this session")
    rows.sort(key=lambda row: row[0])
    return {"source": source, "passages": [{"n": n, "text": text} for n, (_, text) in enumerate(rows, 1)]}
