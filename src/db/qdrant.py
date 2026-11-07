"""Qdrant client + collection lifecycle (create / heal)."""

import os

from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models

from src import clients
from src.config import COLLECTION_NAME

# timeout=60 -> slow TLS handshake par cold start fail na ho
qdrant = QdrantClient(
    url=os.environ["QDRANT_URL"],
    api_key=os.environ["QDRANT_API_KEY"],
    timeout=60,
)

# validate_collection_config=False -> import par koi network call nahi hoti,
# isliye Lambda cold start Qdrant ke bharose nahi rehta.
vector_store = QdrantVectorStore(
    client=qdrant,
    collection_name=COLLECTION_NAME,
    embedding=clients.embeddings,
    validate_collection_config=False,
)

_collection_ready = False


def ensure_collection(force: bool = False) -> None:
    """Collection + payload indexes ensure karo (per container sirf ek baar).

    force=True -> collection delete ho gaya ho to dobara bana do (self-healing),
    warna purana container har request par 500 deta rehta.
    """
    global _collection_ready
    if _collection_ready and not force:
        return
    if not qdrant.collection_exists(COLLECTION_NAME):
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
        )
    # delete() aur session filter ke liye payload index chahiye. Idempotent call.
    for field in ("metadata.source", "metadata.session_id"):
        qdrant.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    _collection_ready = True


def with_heal(call):
    """Ek Qdrant call chalao; collection gayab mile to heal karke dobara try karo."""
    try:
        return call()
    except Exception:
        ensure_collection(force=True)
        return call()
