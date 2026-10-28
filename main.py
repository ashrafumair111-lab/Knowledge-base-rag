import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

import cohere
import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_cohere import CohereEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
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

# Lambda par code dir read-only hota hai -> runtime data /tmp me jaata hai.
# NOTE: /tmp per-instance hota hai, isliye shared registry S3 par rehti hai (USE_S3).
ON_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
DATA_DIR = Path("/tmp/kb-rag") if ON_LAMBDA else PKG_DIR
if ON_LAMBDA:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = DATA_DIR / "uploads"
REGISTRY_PATH = DATA_DIR / "ingested.json"

# S3 (sirf deployed app par) - badi files + sab containers ke liye shared registry
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

# Answer generate karne wala LLM (Gemini) - GOOGLE_API_KEY env se uthata hai.
# Model + timeout env se aate hain (Google models retire hote rehte hain, hardcode nahi).
# temperature set nahi - Gemini 3+ docs explicit temperature ko discourage karte hain.
llm = ChatGoogleGenerativeAI(
    model=os.environ["GEMINI_MODEL"],
    timeout=float(os.environ["LLM_TIMEOUT_SECONDS"]),
    max_retries=2,
)

# Embeddings - semantic/meaning-based search ke liye (1024 dims)
embeddings = CohereEmbeddings(
    model="embed-english-v3.0",
)

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
    embedding=embeddings,
    validate_collection_config=False,
)

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
)

# Upload registry lock - ek hi container me do uploads race na karein
registry_lock = threading.Lock()

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
    """Registry padho (session::file -> sha256/chunks), Lambda par S3 se, locally file se."""
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


def source_chunk_count(source: str, sid: str) -> int:
    """Is session me is source ke kitne chunks abhi maujood hain."""
    return qdrant.count(
        collection_name=COLLECTION_NAME,
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
    ensure_collection()

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

    # Deterministic ids -> re-upload upsert ban jata hai.
    # session_id id me hai, warna do tabs ki same file ek hi point par collide karegi.
    ids = [
        str(uuid.uuid5(PDF_NAMESPACE, f"{sid}::{source}::{doc.page_content}"))
        for doc in docs
    ]

    status = "overwritten" if key in registry else "ingested"

    def write_chunks() -> None:
        # --- OVERWRITE: isi session ke is file ke purane chunks hatao ---
        # (legacy "./naam" source bhi saaf hota hai - purane fixed-PDF wale ingest ke liye)
        qdrant.delete(
            collection_name=COLLECTION_NAME,
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
        vector_store.add_documents(docs, ids=ids)

    with_heal(write_chunks)

    registry[key] = {"sha256": file_hash, "chunks": len(docs)}
    registry_save(registry)

    return {"status": status, "source": source, "chunks": len(docs), "sha256": file_hash}


app = FastAPI()

# Static files (CSS/JS) + Jinja2 chat template
app.mount("/static", StaticFiles(directory=str(PKG_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(PKG_DIR / "templates"))


class AskRequest(BaseModel):
    input: str
    history: list[dict] | None = None


class PresignRequest(BaseModel):
    filename: str
    size: int = 0


class UploadS3Request(BaseModel):
    key: str


def session_id(x_session_id: str | None = Header(default=None, alias="X-Session-Id")) -> str:
    """Har page load ka apna session id.

    Frontend har load (refresh / naya tab) par naya UUID bhejta hai, isliye purane
    tab ke uploads naye tab ko dikhte nahi - har visitor ka knowledge base alag rehta hai.
    """
    return (x_session_id or "default").strip()[:64] or "default"


def ingest_bytes(filename: str, data: bytes, sid: str) -> dict:
    """Bytes ko upload dir me likh kar ingest karo (registry lock ke andar)."""
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    UPLOAD_DIR.mkdir(exist_ok=True)
    dest = UPLOAD_DIR / filename
    dest.write_bytes(data)
    with registry_lock:
        return ingest_file(dest, sha256_bytes(data), sid)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Static files browser cache me stale na rahein.

    Warna purana app.js chal jata hai, X-Session-Id header nahi bhejta, aur saare
    visitors ek hi "default" session me chale jate hain.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.get("/")
def home(request: Request):
    """Simple chat UI (Jinja2 template)."""
    return templates.TemplateResponse(request, "index.html")


@app.post("/upload")
def upload_endpoint(file: UploadFile = File(...), sid: str = Depends(session_id)) -> dict:
    """User ki file lekar Qdrant mein ingest karta hai (skip / ingest / overwrite)."""
    return ingest_bytes(safe_filename(file.filename), file.file.read(), sid)


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
def upload_s3_endpoint(body: UploadS3Request, sid: str = Depends(session_id)) -> dict:
    """Browser S3 me file daal chuka hai - yahan S3 se padh kar ingest hota hai."""
    require_s3()
    if not body.key.startswith("uploads/"):
        raise HTTPException(status_code=400, detail="Invalid upload key")

    data = s3().get_object(Bucket=S3_BUCKET, Key=body.key)["Body"].read()
    # presign ne "<uuid>-<filename>" banaya tha - asli naam wapas nikaalo
    filename = safe_filename(Path(body.key).name.split("-", 1)[-1])
    return ingest_bytes(filename, data, sid)


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
    return with_heal(lambda: vector_store.similarity_search(question, k=20, filter=search_filter))


def history_note(history: list | None) -> str:
    """Chat history ko prompt ke liye chhota text banao (follow-ups jaise 'explain it')."""
    turns = [
        m for m in (history or [])
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
    ]
    if not turns:
        return ""
    lines = [
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
        for m in turns[-6:]
    ]
    return "Recent conversation (use it to resolve follow-ups like 'explain it'): " + " | ".join(lines) + " "


@app.post("/api")
def ask_grok(body: AskRequest, sid: str = Depends(session_id)) -> dict:
    question = body.input

    # --- STEP 1: DENSE search - SIRF isi session ke chunks ---
    dense_docs = find_docs(question, sid)

    if dense_docs:
        # --- STEP 2: RERANKING - results ko query ke against dobara score karo ---
        rerank_result = cohere_client.rerank(
            model="rerank-english-v3.0",
            query=question,
            documents=[doc.page_content for doc in dense_docs],
            top_n=5,
        )
        top_docs = [dense_docs[result.index] for result in rerank_result.results]
        context = "\n".join(doc.page_content for doc in top_docs)

        # "Maine kya upload kiya?" ka jawab isi list se milta hai - alag router ki zaroorat nahi
        files = sorted({str(doc.metadata.get("source")) for doc in dense_docs if doc.metadata.get("source")})
        file_note = "Files uploaded in this session: " + ", ".join(files) + ". "
    else:
        # Is session me abhi koi file nahi - general knowledge se jawab do
        context = "(no document context available)"
        file_note = "No files have been uploaded in this session yet. "

    response = llm.invoke(
        [
            {
                "role": "system",
                "content": (
                    "You are a friendly AI chat assistant with a knowledge base of the user's "
                    f"uploaded files. {file_note}RULES: "
                    "- If the question is about those files, answer from the context below and "
                    "ground your answer in it. "
                    "- Otherwise answer normally from your own knowledge - general facts, "
                    "chit-chat, greetings, coding, advice, anything. "
                    "- Never refuse or say you can't answer just because the answer isn't in the context. "
                    '- Only for questions specifically about the uploaded files where the context '
                    'has no answer, say: "I don\'t know from uploaded PDF." '
                    f"{history_note(body.history)}Context:\n{context}"
                ),
            },
            {"role": "user", "content": question},
        ]
    )

    # .text -> Gemini 3 content blocks (thinking/text) me se sirf answer text deta hai
    return {"content": response.text}


# AWS Lambda entry point - Mangum Lambda event ko FastAPI request banata hai
handler = Mangum(app, lifespan="off")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000)
