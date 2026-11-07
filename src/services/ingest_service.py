"""Ingestion pipeline: extract -> chunk -> embed -> upsert (session scoped)."""

import logging
import threading
from pathlib import Path

from fastapi import HTTPException
from pypdf import PdfReader
from qdrant_client import models

from src import config
from src.db import qdrant as qdrant_db
from src.db.registry import registry_load, registry_save
from src.services import embedder
from src.utils.files import sha256_bytes
from src.utils.text import chunk_id, splitter

logger = logging.getLogger("uvicorn.error")  # uvicorn ke saath wahi console/log

# Upload registry lock - ek hi container me do uploads race na karein
registry_lock = threading.Lock()


def extract_text(path: Path) -> str:
    """PDF se text nikaalo (pypdf) ya TXT/MD ko seedha padho."""
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def source_chunk_count(source: str, sid: str) -> int:
    """Is session me is source ke kitne chunks abhi maujood hain."""
    return qdrant_db.qdrant.count(
        collection_name=config.COLLECTION_NAME,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.session_id",
                    match=models.MatchValue(value=sid),
                ),
                models.FieldCondition(
                    key="metadata.source",
                    match=models.MatchValue(value=source),
                ),
            ]
        ),
    ).count


def ingest_file(path: Path, file_hash: str, sid: str) -> dict:
    """File padho -> chunks banao -> SKIP ya OVERWRITE decide karo.

    - Yehi file (name + sha256) isi session me pehle se hai -> SKIP
    - Naam same, content naya -> OVERWRITE (purane chunks delete, naye insert)
    - Naya file -> ingest

    Sab kuch session ke andar scope hai: ek tab ka upload doosre tab ko affect nahi karta.
    """
    qdrant_db.ensure_collection()

    source = path.name
    registry = registry_load()
    key = f"{sid}::{source}"

    # --- SKIP: same file already in Qdrant -> dobara embed mat karo ---
    if registry.get(key, {}).get("sha256") == file_hash and source_chunk_count(source, sid) > 0:
        return {
            "status": "skipped",
            "source": source,
            "chunks": registry[key].get("chunks", 0),
            "sha256": file_hash,
        }

    text = extract_text(path)
    if not text.strip():
        raise HTTPException(status_code=400, detail=f"No extractable text found in {source}")

    # metadata.source / session_id isliye ke in se purane chunks dhoondh kar delete kar saken
    docs = splitter.create_documents(
        [text], metadatas=[{"source": source, "session_id": sid}]
    )

    # Chunk ka number metadata me - file viewer passages ko padhne ke order me dikhata hai.
    # (id text se banti hai, isliye ye number ids ko nahi badalta.)
    for index, doc in enumerate(docs):
        doc.metadata["chunk"] = index

    # Deterministic ids -> re-upload upsert ban jata hai.
    ids = [chunk_id(sid, source, doc.page_content) for doc in docs]

    status = "overwritten" if key in registry else "ingested"

    def write_chunks() -> None:
        # --- OVERWRITE: isi session ke is file ke purane chunks hatao ---
        # (legacy "./naam" source bhi saaf hota hai - purane fixed-PDF wale ingest ke liye)
        qdrant_db.qdrant.delete(
            collection_name=config.COLLECTION_NAME,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.session_id",
                        match=models.MatchValue(value=sid),
                    ),
                    models.FieldCondition(
                        key="metadata.source",
                        match=models.MatchAny(any=[source, f"./{source}"]),
                    ),
                ]
            ),
        )
        # --- phir fresh chunks daalo (ids dene se ye upsert ban jata hai) ---
        # Batch + pacing: poora document ek hi call me bhejne se Cohere ka
        # tokens/minute limit cross ho jata hai. Chote batches ke beech wait karte hain.
        for start in range(0, len(docs), config.EMBED_BATCH_SIZE):
            batch_docs = docs[start : start + config.EMBED_BATCH_SIZE]
            batch_ids = ids[start : start + config.EMBED_BATCH_SIZE]
            batch_chars = sum(len(doc.page_content) for doc in batch_docs)
            logger.info(
                "embed %s: batch %d-%d/%d", source, start + 1, start + len(batch_docs), len(docs)
            )
            qdrant_db.vector_store.add_documents(batch_docs, ids=batch_ids)
            # aakhri batch ke baad wait ki zaroorat nahi
            if start + config.EMBED_BATCH_SIZE < len(docs):
                embedder._pace(batch_chars)

    qdrant_db.with_heal(write_chunks)

    registry[key] = {"sha256": file_hash, "chunks": len(docs)}
    registry_save(registry)

    return {"status": status, "source": source, "chunks": len(docs), "sha256": file_hash}


def ingest_bytes(filename: str, data: bytes, sid: str) -> dict:
    """Bytes ko upload dir me likh kar ingest karo (registry lock ke andar)."""
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    config.UPLOAD_DIR.mkdir(exist_ok=True)
    dest = config.UPLOAD_DIR / filename
    dest.write_bytes(data)
    with registry_lock:
        return ingest_file(dest, sha256_bytes(data), sid)
