import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
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
# NOTE: /tmp per-instance hota hai, isliye shared registry S3 par rehti hai (USE_S3).
ON_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
DATA_DIR = Path("/tmp/kb-rag") if ON_LAMBDA else PKG_DIR
if ON_LAMBDA:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = DATA_DIR / "uploads"
REGISTRY_PATH = DATA_DIR / "ingested.json"

# S3 (sirf deployed app/Lambda par) - badi files + sab containers ke liye shared registry
S3_BUCKET = os.environ.get("S3_BUCKET", "")
REGISTRY_KEY = "_registry/ingested.json"
USE_S3 = ON_LAMBDA and bool(S3_BUCKET)

# Sirf yehi file types accept hote hain (pypdf / plain text)
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}

# Chunk ka point ID uske text se banta hai (deterministic) -> overwrite/upsert reliable.
# NOTE: ye UUID ek baar set karne ke baad KABHI change nahi karni.
PDF_NAMESPACE = uuid.UUID("a06eab4d-f20e-48c3-af71-b62597a0dc5b")

# Cohere client - reranking (query ke against chunks ko dobara score karne) ke liye
cohere_client = cohere.Client(api_key=os.environ["COHERE_API_KEY"])

# Answer generate karne wala LLM (Groq)
llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
    max_retries=2,
)

# Embeddings - semantic/meaning-based search ke liye
embeddings = CohereEmbeddings(
    model="embed-english-v3.0",
)

# Qdrant client - collection na ho to khud bana dete hain
# (1024 dims = embed-english-v3.0, cosine distance)
qdrant = QdrantClient(
    url=os.environ["QDRANT_URL"],
    api_key=os.environ["QDRANT_API_KEY"],
)

def ensure_collection() -> None:
    """Collection + payload index ensure karo (idempotent, self-healing).

    Qdrant me collection delete karne par app khud dobara bana deta hai -
    warna warm Lambda container har upload/ask par 500 deta rehta.
    """
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


# Startup par ensure (fast-fail); har upload/ask par dobara (heal agar collection delete ho)
ensure_collection()

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


def s3():
    """S3 client - Lambda runtime me boto3 pre-installed hota hai."""
    import boto3

    return boto3.client("s3")


def require_s3() -> None:
    """S3 sirf deployed app par hai - local dev par 400 do."""
    if not USE_S3:
        raise HTTPException(
            status_code=400, detail="S3 upload is only available on the deployed app"
        )


def safe_filename(name: str) -> str:
    """Base filename + extension check (ek saath path-traversal aur type validation)."""
    filename = Path(name or "").name
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Only PDF, TXT and MD files are supported"
        )
    return filename


def registry_load() -> dict:
    """Registry padho (file -> sha256/chunks/uploaded_at + "_last" pointer).

    Lambda par S3 se (sab containers ke liye EK hi copy), locally ingested.json se.
    """
    if USE_S3:
        try:
            body = s3().get_object(Bucket=S3_BUCKET, Key=REGISTRY_KEY)["Body"].read()
            return json.loads(body)
        except Exception:
            return {}
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def registry_save(registry: dict) -> None:
    """Registry persist karo - Lambda par S3, locally ingested.json."""
    data = json.dumps(registry, indent=2)
    if USE_S3:
        s3().put_object(Bucket=S3_BUCKET, Key=REGISTRY_KEY, Body=data.encode("utf-8"))
    else:
        REGISTRY_PATH.write_text(data, encoding="utf-8")


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
    ensure_collection()

    source = path.name
    registry = registry_load()

    # --- SKIP: same file already in Qdrant -> dobara embed mat karo ---
    if registry.get(source, {}).get("sha256") == file_hash and source_chunk_count(source) > 0:
        # Yehi file dobara upload hui - "recent upload" yahi mana jayega
        registry["_last"] = source
        registry_save(registry)
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

    # --- OVERWRITE: is file ke SAARE purane chunks Qdrant se hatao ---
    # (legacy "./naam" source bhi saaf hota hai - purane fixed-PDF wale ingest ke liye)
    # ids se alag delete ki zaroorat nahi - ye filter already un points ko pakad leta hai.
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

    registry[source] = {
        "sha256": file_hash,
        "chunks": len(docs),
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "preview": text[:300],
    }
    registry["_last"] = source  # "recent upload" pointer - "what did I upload" isi se milta hai
    registry_save(registry)

    return {"status": status, "source": source, "chunks": len(docs), "sha256": file_hash}


app = FastAPI()

# Static files (CSS/JS) + Jinja2 chat template
app.mount("/static", StaticFiles(directory=str(PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(PKG_DIR / "templates"))


class AskRequest(BaseModel):
    input: str
    history: list[dict] | None = None


def ingest_bytes(filename: str, data: bytes) -> dict:
    """Bytes ko upload dir me likh kar ingest karo (registry lock ke andar)."""
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    UPLOAD_DIR.mkdir(exist_ok=True)
    dest = UPLOAD_DIR / filename
    dest.write_bytes(data)
    with registry_lock:
        return ingest_file(dest, sha256_bytes(data))


@app.get("/")
def home(request: Request):
    """Simple chat UI (Jinja2 template)."""
    return templates.TemplateResponse(request, "index.html")


@app.post("/upload")
def upload_endpoint(file: UploadFile = File(...)) -> dict:
    """User ki file lekar Qdrant mein ingest karta hai (skip / ingest / overwrite)."""
    return ingest_bytes(safe_filename(file.filename), file.file.read())


class PresignRequest(BaseModel):
    filename: str
    size: int = 0


class UploadS3Request(BaseModel):
    key: str


@app.post("/presign")
def presign_endpoint(body: PresignRequest) -> dict:
    """Browser ko temporary S3 PUT URL - badi files Lambda ke 6MB limit se bach jati hain."""
    require_s3()
    filename = safe_filename(body.filename)
    if body.size and body.size > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 200 MB)")

    key = f"uploads/{uuid.uuid4().hex}-{filename}"
    url = s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=300,
    )
    return {"upload_url": url, "key": key}


@app.post("/upload-s3")
def upload_s3_endpoint(body: UploadS3Request) -> dict:
    """Browser S3 me file daal chuka hai - yahan S3 se padh kar ingest hota hai."""
    require_s3()
    if not body.key.startswith("uploads/"):
        raise HTTPException(status_code=400, detail="Invalid upload key")

    data = s3().get_object(Bucket=S3_BUCKET, Key=body.key)["Body"].read()
    # presign ne "<uuid>-<filename>" banaya tha - asli naam wapas nikaalo
    filename = safe_filename(Path(body.key).name.split("-", 1)[-1])
    return ingest_bytes(filename, data)


INTENT_GATE_PATTERNS = (
    r"\bupload", r"\bfile", r"\bpdf", r"\bdocument", r"\bdoc\b",
    r"\bdala", r"\bdaala", r"\bdiya", r"\bshamil", r"\bjama", r"\bdale",
    r"\bexplain", r"\bdescrib", r"\bsummar", r"\bdetail", r"\btell me",
    r"\bit\b", r"\bthis\b", r"\bthat\b", r"\bone\b",
)


def needs_intent_check(question: str) -> bool:
    """Upload/follow-up jaise sawalon par AI se classification - seedhe factual sawal par extra call nahi."""
    q = question.lower()
    return any(re.search(p, q) for p in INTENT_GATE_PATTERNS)


def format_history(history: list | None, line_limit: int = 600) -> str:
    """Chat history ko prompt ke liye chhota text banao (follow-ups jaise 'explain it' ke liye)."""
    if not history:
        return ""
    lines = []
    for m in history[-6:]:
        who = "User" if m.get("role") == "user" else "Assistant"
        lines.append(f"{who}: {str(m.get('content', ''))[:line_limit]}")
    return " | ".join(lines)


def route_intent(question: str, file_names: list, last: str | None, history_text: str = "") -> str:
    """AI decide karta hai: meta / recent / normal / file:<naam>. History se follow-ups resolve hote hain."""
    response = llm.invoke(
        [
            {
                "role": "system",
                "content": (
                    "You are an intent classifier. Reply with exactly ONE token: "
                    "meta, recent, normal, or file:<filename>. "
                    f"Known uploaded files: {', '.join(file_names)}. "
                    f"Most recently uploaded: {last or 'none'}. "
                    + (f"Recent conversation: {history_text}. " if history_text else "")
                    + "Classify the USER QUESTION: "
                    "- meta: WHAT they uploaded, which file, or when - not its content. "
                    "- recent: the CONTENT/summary/meaning of the file they uploaded, or refers to it "
                    "with 'it'/'this'/'the one' without naming a file - use the recent upload. "
                    "- file:<filename>: clearly refers to ONE specific known file, by name or as the "
                    "obvious topic of the last turns - use exactly that file. "
                    "- normal: any other question, not about a specific uploaded file."
                ),
            },
            {"role": "user", "content": question},
        ]
    )
    ans = (response.content or "").strip().lower()
    if "file:" in ans:
        name = ans.split("file:", 1)[1].strip().rstrip(". ,")
        for known in file_names:
            if known.lower() == name or (name and name in known.lower()):
                return "file:" + known
    if "meta" in ans:
        return "meta"
    if "recent" in ans:
        return "recent"
    return "normal"


def last_uploaded_source(registry: dict) -> str | None:
    """_last pointer, ya purani registry ke liye sabse nayi uploaded_at entry."""
    last = registry.get("_last")
    if last:
        return last
    files = {k: v for k, v in registry.items() if not k.startswith("_")}
    if not files:
        return None
    return max(files.items(), key=lambda kv: kv[1].get("uploaded_at", ""))[0]


@app.post("/api")
def ask_grok(body: AskRequest) -> dict:
    question = body.input
    ensure_collection()

    registry = registry_load()
    last = last_uploaded_source(registry)

    # Intent: AI khud decide karta hai - meta (kya upload kiya) / recent (us recent file ka content) / normal
    intent = "normal"
    file_names = [k for k in registry if not k.startswith("_")]
    history = [
        m for m in (body.history or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]
    if file_names and needs_intent_check(question):
        try:
            intent = route_intent(question, file_names, last, format_history(history))
        except Exception:
            intent = "normal"  # classification fail -> normal RAG (kuch chhupta nahi)

    # --- meta: "Maine kya upload kiya?" -> RAG nahi, registry seedha jawab ---
    if intent == "meta":
        if not last or last not in registry:
            return {"content": "Abhi tak koi file upload nahi hui."}
        info = registry[last]
        context = (
            f"Most recently uploaded file: {last}\n"
            f"Type: {Path(last).suffix.upper().lstrip('.')}\n"
            f"Chunks in knowledge base: {info.get('chunks', 'unknown')}\n"
            f"Uploaded at (UTC): {info.get('uploaded_at', 'unknown')}"
            f" Preview (first 300 chars): {info.get('preview', 'n/a')}"
        )
        response = llm.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant. Using ONLY the file information in the context, "
                        "answer the user's question about what they uploaded. Be concise. "
                        "If they ask what the file contains, say you can answer questions about its content."
                        f"\ncontext:{context}"
                    ),
                },
                {"role": "user", "content": question},
            ]
        )
        return {"content": response.content}

    # --- STEP 1: DENSE (semantic/meaning-based) search - Qdrant se ---
    # recent/file intent -> SIRF usi file ke chunks; 0 results ho to poore KB par fallback
    source_filter = None
    if intent == "recent" and last and last in registry:
        source_filter = last
    elif intent.startswith("file:"):
        candidate = intent[5:]
        if candidate in registry:
            source_filter = candidate

    dense_docs = []
    if source_filter:
        dense_docs = vector_store.similarity_search(
            question,
            k=20,
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.source",
                        match=models.MatchValue(value=source_filter),
                    )
                ]
            ),
        )
    if not dense_docs:
        dense_docs = vector_store.similarity_search(question, k=20)

    # Knowledge base khali hai - abhi tak koi file upload nahi hui.
    # Chatbot ki tarah general/chit-chat questions phir bhi answer karo (bina context ke).
    if not dense_docs:
        response = llm.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a friendly, helpful AI chat assistant. "
                        "The knowledge base is currently empty (no files uploaded yet), "
                        "so answer general questions from your own knowledge. "
                        "If asked about uploaded documents, mention that no file has been uploaded yet."
                    ),
                },
                {"role": "user", "content": question},
            ]
        )
        return {"content": response.content}

    # --- STEP 2: RERANKING - results ko query ke against dobara score karo ---
    rerank_result = cohere_client.rerank(
        model="rerank-english-v3.0",
        query=question,
        documents=[doc.page_content for doc in dense_docs],
        top_n=5,
    )

    top_docs = [dense_docs[result.index] for result in rerank_result.results]
    context = "\n".join(doc.page_content for doc in top_docs)

    # filtered path: model ko saaf batao ki context KIS file ka hai -
    # warna wo strict rules dekh kar galat "I don't know" bol deta hai
    notes = ""
    if source_filter:
        notes += (
            f"NOTE: this conversation is about '{source_filter}'. The context below IS that "
            "same file's content - answer from it directly. "
        )
    if history:
        notes += (
            "Recent conversation (resolve follow-ups like 'explain it' from it): "
            + format_history(history, 400)
            + " "
        )

    response = llm.invoke(
        [
            {
                "role": "system",
                "content": (
                    "You are a friendly AI chat assistant with a knowledge base of the user's "
                    "uploaded files. RULES: "
                    "- If the question is about those documents or the context is relevant, answer "
                    "from the context (ground your answer in it). "
                    "- Otherwise answer normally from your own knowledge - general facts, chit-chat, "
                    "greetings, coding, advice, anything. "
                    "- Never refuse or say you can't answer just because the answer isn't in the context. "
                    '- Only for questions specifically about the uploaded files where the context has '
                    'no answer, say: "I don\'t know from uploaded PDF." '
                    f"{notes}context:{context}"
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
