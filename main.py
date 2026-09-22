import os
import threading
import uuid

import cohere
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_cohere import CohereEmbeddings
from langchain_groq import ChatGroq
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel
from pypdf import PdfReader
from qdrant_client import QdrantClient, models

load_dotenv()

COLLECTION_NAME = "grocery store"
PDF_PATH = "./knowledge.pdf"

# Fixed namespace: har chunk ka point ID uske text se banaya jata hai, isliye
# same chunk ka ID hamesha same rehta hai -> overwrite (upsert) reliable hota hai.
# NOTE: ye UUID ek baar set karne ke baad KABHI change nahi karni.
PDF_NAMESPACE = uuid.UUID("a06eab4d-f20e-48c3-af71-b62597a0dc5b")

# Cohere client - reranking (query ke against chunks ko dobara score karne) ke liye
cohere_client = cohere.Client(api_key=os.environ["COHERE_API_KEY"])

# Answer generate karne wala LLM (Groq) - bilkul wahi model jo index.js mein tha
llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
    max_retries=2,
)

# Embeddings - semantic/meaning-based search ke liye
# NOTE: Python SDK mein batch_size ka option nahi hai (wo internal handle hota hai),
# isliye yahan sirf wahi model diya gaya hai jo index.js mein tha.
embeddings = CohereEmbeddings(
    model="embed-english-v3.0",
)

# Qdrant client - collection na ho to khud bana dete hain
# (1024 dims = embed-english-v3.0, cosine distance)
qdrant = QdrantClient(
    url=os.environ["QDRANT_URL"],
    api_key=os.environ["QDRANT_API_KEY"],
)

if not qdrant.collection_exists(COLLECTION_NAME):
    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
    )

# "metadata.source" par filter karne ke liye Qdrant ko payload index chahiye,
# warna delete() 400 Bad Request deta hai. Ye call idempotent hai.
qdrant.create_payload_index(
    collection_name=COLLECTION_NAME,
    field_name="metadata.source",
    field_schema=models.PayloadSchemaType.KEYWORD,
)

# Dense vector store (Qdrant) - ye SEMANTIC search ka engine hai
vector_store = QdrantVectorStore(
    client=qdrant,
    collection_name=COLLECTION_NAME,
    embedding=embeddings,
)


def upload() -> dict:
    """PDF padho -> chunks banao -> purane chunks DELETE -> naye chunks INSERT (overwrite)."""
    reader = PdfReader(PDF_PATH)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )

    # metadata.source isliye ke is se is PDF ke purane chunks dhoondh kar delete kar saken
    docs = splitter.create_documents([text], metadatas=[{"source": PDF_PATH}])

    # Har chunk ka point ID uske text se banaya (deterministic -> upsert reliable)
    ids = [
        str(uuid.uuid5(PDF_NAMESPACE, f"{PDF_PATH}::{doc.page_content}"))
        for doc in docs
    ]

    # --- OVERWRITE: pehle is PDF ke purane chunks Qdrant se hatao ---
    qdrant.delete(collection_name=COLLECTION_NAME, points_selector=ids)
    qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.source",
                    match=models.MatchValue(value=PDF_PATH),
                )
            ]
        ),
    )

    # --- phir fresh chunks daalo (ids dene se ye upsert ban jata hai) ---
    vector_store.add_documents(docs, ids=ids)

    return {"status": "overwritten", "chunks": len(docs), "source": PDF_PATH}


_ingest_state = {"done": False}
_ingest_lock = threading.Lock()


def ensure_ingested() -> None:
    """Sirf process ke PEHLE /api call par knowledge.pdf load/overwrite karta hai.

    Uske baad ki calls no-op hain. Agar upload fail ho jaye to flag False rehta hai,
    isliye agli call par dobara koshish hoti hai.
    """
    if _ingest_state["done"]:
        return

    with _ingest_lock:
        if _ingest_state["done"]:
            return

        print(f"[ingest] {upload()}")
        _ingest_state["done"] = True


app = FastAPI()


class AskRequest(BaseModel):
    input: str


@app.post("/api")
def ask_grok(body: AskRequest) -> dict:
    question = body.input

    # Pehli /api call par knowledge.pdf load/overwrite hota hai; uske baad no-op
    ensure_ingested()

    # --- STEP 1: DENSE (semantic/meaning-based) search - Qdrant se ---
    dense_docs = vector_store.similarity_search(question, k=20)

    # --- STEP 2: RERANKING - results ko query ke against dobara score karo ---
    rerank_result = cohere_client.rerank(
        model="rerank-english-v3.0",
        query=question,
        documents=[doc.page_content for doc in dense_docs],
        top_n=5,
    )

    top_docs = [dense_docs[result.index] for result in rerank_result.results]
    context = "\n".join(doc.page_content for doc in top_docs)

    response = llm.invoke(
        [
            {
                "role": "system",
                "content": (
                    "You are a RAG AI assistant. STRICT RULES: "
                    "- Answer ONLY from context "
                    "- Do not use outside knowledge "
                    '- If answer not found say: "I don\'t know from uploaded PDF." '
                    f"context:{context}"
                ),
            },
            {"role": "user", "content": question},
        ]
    )

    return {"content": response.content}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000)
