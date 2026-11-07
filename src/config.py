"""App configuration: env vars, filesystem paths aur constants.

Ye module sabse neeche hai - kisi bhi doosre app module ko import nahi karta,
isliye yahan se shuru karna safe hai (koi circular import nahi).
"""

import os
import secrets
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

COLLECTION_NAME = "grocery store"

# src/ aur project root. Templates/static src/ ke andar hain (Lambda par READ-ONLY).
SRC_DIR = Path(__file__).resolve().parent
BASE_DIR = SRC_DIR.parent
VIEWS_DIR = SRC_DIR / "views"
ASSETS_DIR = SRC_DIR / "assets"

# Lambda par code dir read-only hota hai -> runtime data /tmp me jaata hai.
# NOTE: /tmp per-instance hota hai, isliye shared registry S3 par rehti hai (USE_S3).
ON_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
DATA_DIR = Path("/tmp/kb-rag") if ON_LAMBDA else BASE_DIR
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

# --- Session cookie ---------------------------------------------------------
# Server har browser ko ek signed, HttpOnly cookie deta hai -> refresh/restart par wahi session
# milta hai aur uploaded files wapas dikhti hain. Cookie stateless hai (HMAC), isliye Lambda ke
# kisi bhi container par verify ho jati hai. Secret sirf env me rehta hai.
SESSION_COOKIE = "kb_sid"
SESSION_MAX_AGE = 14 * 24 * 3600  # seconds; har page load par naya 14 din (sliding)

# Lambda par SESSION_SECRET zaroori hai (warna har container alag secret banata aur sessions toot te).
# Local dev me na ho to process-level random secret chalta hai - restart par naya session.
SESSION_SECRET = (
    os.environ["SESSION_SECRET"]
    if ON_LAMBDA
    else os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
)

# Rerank score (0-1) se kam wale chunks ko "source" nahi gina jaata - weak match citation nahi banta
SOURCE_MIN_SCORE = 0.3

# Answer generate karne wala LLM (Groq) - GROQ_API_KEY env se uthata hai.
GROQ_MODEL = "openai/gpt-oss-120b"

# --- Embedding pacing -------------------------------------------------------
# Cohere ki limit "tokens per minute" hai (trial: 100k), requests per minute nahi.
# Isliye bara document ek hi dafa bhejne se 429 aata hai aur upload fail ho jata hai.
# Chunks chote batches me bhejte hain aur har batch ke baad tokens ke hisaab se
# wait karte hain, taake minute budget cross na ho.
EMBED_BATCH_SIZE = 32
EMBED_TOKENS_PER_MINUTE = 80_000  # limit (100k) se neeche - safety margin
CHARS_PER_TOKEN = 4  # English text ka mota-moti estimate
