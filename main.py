import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

import cohere
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_cohere import CohereEmbeddings
from langchain_groq import ChatGroq
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from mangum import Mangum
from pydantic import BaseModel
from pypdf import PdfReader
from qdrant_client import QdrantClient, models
from starlette.requests import Request

load_dotenv()

COLLECTION_NAME = "grocery store"

# Package dir = code/templates/static ka asli location (Lambda par READ-ONLY hota hai)
PKG_DIR = Path(__file__).resolve().parent

# Lambda par code dir read-only hota hai -> runtime data hamesha /tmp me jaata hai.
# NOTE: /tmp har Lambda instance ka alag hota hai -> registry per-instance rahegi.
# Duplicates phir bhi NAHI bante (delete-by-source + deterministic ids chalte rehte hain),
# bas ek naya instance pehli baar dobara embed kar sakta hai.
ON_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
DATA_DIR = Path("/tmp/kb-rag") if ON_LAMBDA else PKG_DIR
if ON_LAMBDA:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = DATA_DIR / "uploads"
REGISTRY_PATH = DATA_DIR / "ingested.json"

# Sirf yehi file types accept hote hain (pypdf / plain text)
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}

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


splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
)

# Upload registry lock - ek saath do uploads par registry/overwrite race na ho
registry_lock = threading.Lock()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def registry_load() -> dict:
    """ingested.json padho (source -> sha256 + chunks). Corrupt ho to fresh start."""
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def extract_text(path: Path) -> str:
    """PDF se text nikaalo (pypdf) ya TXT/MD ko seedha padho."""
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def source_chunk_count(source: str) -> int:
    """Qdrant mein is source ke kitne chunks abhi maujood hain."""
    return qdrant.count(
        collection_name=COLLECTION_NAME,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.source",
                    match=models.MatchValue(value=source),
                )
            ]
        ),
    ).count


def ingest_file(path: Path, file_hash: str) -> dict:
    """File padho -> chunks banao -> SKIP ya OVERWRITE decide karo.

    - Agar yehi file (name + sha256) pehle se Qdrant mein hai -> SKIP (koi kaam nahi)
    - Agar naam same lekin content naya -> OVERWRITE (purane chunks delete, naye insert)
    - Bilkul naya file -> ingest
    """
    source = path.name
    registry = registry_load()

    # --- SKIP: same file already in Qdrant -> dobara embed mat karo ---
    if registry.get(source, {}).get("sha256") == file_hash and source_chunk_count(source) > 0:
        return {
            "status": "skipped",
            "source": source,
            "chunks": registry[source].get("chunks", 0),
            "sha256": file_hash,
        }

    text = extract_text(path)
    if not text.strip():
        raise HTTPException(status_code=400, detail=f"No extractable text found in {source}")

    # metadata.source isliye ke is se is file ke purane chunks dhoondh kar delete kar saken
    docs = splitter.create_documents([text], metadatas=[{"source": source}])

    # Har chunk ka point ID uske text se banaya (deterministic -> upsert reliable)
    ids = [
        str(uuid.uuid5(PDF_NAMESPACE, f"{source}::{doc.page_content}"))
        for doc in docs
    ]

    status = "overwritten" if source in registry else "ingested"

    # --- OVERWRITE: pehle is file ke purane chunks Qdrant se hatao ---
    # (legacy "./naam" source bhi saaf hota hai - purane fixed-PDF wale ingest ke liye)
    qdrant.delete(collection_name=COLLECTION_NAME, points_selector=ids)
    qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.source",
                    match=models.MatchAny(any=[source, f"./{source}"]),
                )
            ]
        ),
    )

    # --- phir fresh chunks daalo (ids dene se ye upsert ban jata hai) ---
    vector_store.add_documents(docs, ids=ids)

    registry[source] = {"sha256": file_hash, "chunks": len(docs)}
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    return {"status": status, "source": source, "chunks": len(docs), "sha256": file_hash}


app = FastAPI()

# Static files (CSS/JS) + Jinja2 chat template
app.mount("/static", StaticFiles(directory=str(PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(PKG_DIR / "templates"))


class AskRequest(BaseModel):
    input: str


@app.get("/")
def home(request: Request):
    """Simple chat UI (Jinja2 template)."""
    return templates.TemplateResponse(request, "index.html")


@app.post("/upload")
def upload_endpoint(file: UploadFile = File(...)) -> dict:
    """User ki file lekar Qdrant mein ingest karta hai (skip / ingest / overwrite)."""
    # .name path traversal se bachata hai (Windows/Linux dono par)
    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF, TXT and MD files are supported")

    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    UPLOAD_DIR.mkdir(exist_ok=True)
    dest = UPLOAD_DIR / filename
    dest.write_bytes(data)

    with registry_lock:
        return ingest_file(dest, sha256_bytes(data))


@app.post("/api")
def ask_grok(body: AskRequest) -> dict:
    question = body.input

    # --- STEP 1: DENSE (semantic/meaning-based) search - Qdrant se ---
    dense_docs = vector_store.similarity_search(question, k=20)

    # Knowledge base khali hai - abhi tak koi file upload nahi hui
    if not dense_docs:
        return {"content": "I don't know from uploaded PDF."}

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
                    "- Answer ONLY from context yes but you can also answer basic questions hi how are you like these etc"
                    "- Do not use outside knowledge "
                    '- If answer not found say: "I don\'t know from uploaded PDF." '
                    f"context:{context}"
                ),
            },
            {"role": "user", "content": question},
        ]
    )

    return {"content": response.content}


# AWS Lambda entry point - Mangum Lambda event ko FastAPI request banata hai
handler = Mangum(app, lifespan="off")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000)
