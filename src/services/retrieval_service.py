"""Retrieval pipeline: dense search (session scoped) + Cohere rerank."""

from qdrant_client import models

from src import clients, config
from src.db import qdrant as qdrant_db


def find_docs(question: str, sid: str) -> list:
    """Session ke andar dense search; collection gayab ho to ek baar heal karke retry."""
    search_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.session_id",
                match=models.MatchValue(value=sid),
            )
        ]
    )
    return qdrant_db.with_heal(
        lambda: qdrant_db.vector_store.similarity_search(question, k=20, filter=search_filter)
    )


def rerank(question: str, dense_docs: list) -> tuple[str, list[str], str]:
    """Rerank results -> (context, citation sources, file note)."""
    # --- STEP 2: RERANKING - results ko query ke against dobara score karo ---
    rerank_result = clients.cohere_client.rerank(
        model="rerank-english-v3.0",
        query=question,
        documents=[doc.page_content for doc in dense_docs],
        top_n=5,
    )
    top_docs = [dense_docs[result.index] for result in rerank_result.results]
    context = "\n".join(doc.page_content for doc in top_docs)

    # Jawab ke "From <file>" chips: sirf relevant chunks ki files, duplicate nahi
    sources: list[str] = []
    for result, doc in zip(rerank_result.results, top_docs):
        source = doc.metadata.get("source")
        if source and getattr(result, "relevance_score", 0.0) >= config.SOURCE_MIN_SCORE and source not in sources:
            sources.append(str(source))

    # "Maine kya upload kiya?" ka jawab isi list se milta hai - alag router ki zaroorat nahi
    files = sorted({str(doc.metadata.get("source")) for doc in dense_docs if doc.metadata.get("source")})
    file_note = "Files uploaded in this session: " + ", ".join(files) + ". "
    return context, sources, file_note
