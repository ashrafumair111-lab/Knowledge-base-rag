# Knowledge Base RAG

A question-answering service that answers questions **strictly from an uploaded PDF document**
using Retrieval-Augmented Generation (RAG).

This is the Python/FastAPI implementation of the original Node.js/Express RAG service.
The pipeline is identical, with one deliberate simplification:
**hybrid search has been removed**, so retrieval is purely **semantic (dense vector) search**.

---

## Contents

- [What it does](#what-it-does)
- [Pipeline](#pipeline)
- [Ingestion behaviour](#ingestion-behaviour)
- [Technology stack](#technology-stack)
- [Requirements](#requirements)
- [Setup](#setup)
- [How to run](#how-to-run)
- [API reference](#api-reference)
- [Project structure](#project-structure)
- [Configuration](#configuration)
- [How a request is processed](#how-a-request-is-processed)
- [Notes and limitations](#notes-and-limitations)

---

## What it does

1. **On upload** (`POST /upload` from the web UI or curl), the service reads the file
   (PDF / TXT / MD), splits it into overlapping text chunks, embeds them with Cohere, and
   stores them in the Qdrant collection. Uploading is **idempotent**:
   - Same file again (same name + same sha256) → **skipped**, nothing is re-embedded.
   - Same file name, new content → old chunks are **overwritten** (deleted + re-inserted).
2. **On every question** it embeds the query, retrieves the 20 most semantically similar
   chunks across **all** uploaded files, re-ranks them with Cohere, and keeps the best 5.
3. A Groq-hosted LLM generates the final answer using **only** that retrieved context.
   If the answer is not present in the documents, it replies:
   `I don't know from uploaded PDF.`

A simple **web UI** (Jinja2 + vanilla HTML/CSS/JS) is served at `GET /` — chat with the
knowledge base and upload files from the browser.

---

## Pipeline

```
FIRST POST /api CALL OF EACH PROCESS (runs once, then never again)
  knowledge.pdf
      -> pypdf                          extract plain text
      -> RecursiveCharacterTextSplitter chunk_size=1000, chunk_overlap=200
      -> deterministic ids              uuid5(PDF_NAMESPACE, source + chunk text)
      -> Qdrant DELETE                  remove this document's existing chunks
      -> Cohere embed-english-v3.0      embed each chunk
      -> Qdrant upsert                  insert fresh chunks => always exactly 7 points

EVERY POST /api CALL
  question
      -> Cohere embed-english-v3.0      embed the query
      -> Qdrant similarity_search(k=20) semantic / dense retrieval
      -> Cohere rerank-english-v3.0     re-rank, keep top_n=5
      -> Groq openai/gpt-oss-120b      strict prompt
      -> { "content": "..." }
```

> **Note:** the hybrid (dense + BM25 keyword) retrieval path and its
> Reciprocal Rank Fusion step from the Node.js version are intentionally not implemented here.

---

## Technology stack

| Concern | Choice |
|---|---|
| Web framework | FastAPI + Uvicorn |
| Runtime | Python 3.12 |
| Package manager | uv |
| Embeddings | Cohere `embed-english-v3.0` (1024 dimensions) |
| Vector database | Qdrant, collection `grocery store`, cosine distance |
| Re-ranking | Cohere `rerank-english-v3.0`, `top_n=5` |
| LLM | Groq `openai/gpt-oss-120b`, `temperature=0` |
| PDF parsing | pypdf |
| Text splitting | LangChain `RecursiveCharacterTextSplitter` |

---

## Requirements

- **uv** — it will download and manage Python 3.12 automatically.
- A **Cohere** API key (used for embeddings and re-ranking).
- A **Groq** API key (used for answer generation).
- A **Qdrant** instance (URL and API key). The collection is created automatically if it
  does not exist, together with the payload index needed for filtering.

---

## Setup

### 1. Install uv

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify the installation:

```powershell
uv --version
```

### 2. Create the environment file

Create a `.env` file in the project root (this file is git-ignored and must never be committed):

```ini
GROQ_API_KEY=your_groq_api_key
COHERE_API_KEY=your_cohere_api_key
QDRANT_URL=https://your-cluster.region.aws.cloud.qdrant.io
QDRANT_API_KEY=your_qdrant_api_key
```

### 3. Install  dependencies

```powershell
uv sync
```

This creates a `.venv` directory, installs Python 3.12 if necessary, and installs every
dependency pinned in `uv.lock`.

---

## How to run

### Option A — standard run

```powershell
uv run main.py
```

The service listens on **port 3000**. Startup is complete when you see:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:3000 (Press CTRL+C to quit)
```

`Application startup complete.` confirms the server is ready. Open
**http://localhost:3000** in a browser for the chat UI (upload a file, then ask questions).
The first start after a fresh install can take around 30 seconds while
Windows scans the newly installed binaries; subsequent starts take roughly 10 seconds.

### Option B — development mode (auto-reload)

```powershell
uv run uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

The server restarts automatically whenever a source file changes. This is the equivalent
of `npm run dev` (`nodemon index.js`) in the Node.js version.

### Stopping the server

Press `CTRL+C` in the terminal.

---

## API reference

### `GET /`

Serves the web UI (Jinja2 chat page with file upload and message input).

### `POST /upload`

Uploads a file (PDF, TXT or MD) and ingests it into Qdrant.

- **Same file uploaded again** (same name + same sha256) → `"skipped"`, no re-embedding.
- **Same name, new content** → `"overwritten"` (old chunks deleted, new ones inserted).
- **New file** → `"ingested"`.

**Response — `200 OK`**

```json
{ "status": "ingested", "source": "knowledge.pdf", "chunks": 7, "sha256": "9c1c..." }
```

**Example — curl**

```bash
curl -X POST http://localhost:3000/upload -F "file=@./knowledge.pdf"
```

### `POST /api`

Answers a question using the ingested PDF as the only source of truth.

**Request**

```json
{ "input": "What is the price range of rice?" }
```

**Response — `200 OK`**

```json
{
  "content": "The catalog lists two types of rice: Basmati rice (1 kg) at Rs 220-400 and IRRI rice (1 kg) at Rs 130-220.",
  "sources": ["catalog.pdf"]
}
```

`sources` lists the files behind the answer: re-ranked chunks scoring at least `SOURCE_MIN_SCORE`
(in `main.py`), de-duplicated. It is empty when no file was relevant.

### Sessions and `GET /session/files`

The page load (`GET /`) gives each browser a signed, `HttpOnly`, `SameSite=Lax` cookie `kb_sid`
(`Secure` over HTTPS, 14 days, renewed on every page load). It is the session id, so a refresh
keeps the same knowledge base, and `GET /session/files` returns `{"files": [{"source", "chunks"}]}`
so the sidebar list is restored. The cookie is signed with `SESSION_SECRET`; a forged or edited
cookie is ignored. API clients without a cookie can still send `X-Session-Id` (it takes
precedence); with neither they share the `default` session, as before.

### `POST /api/stream`

Same request, headers and session filtering as `/api`, but the answer arrives as Server-Sent
Events (`text/event-stream`) while it is being generated: `token` events (`{"text": "..."}`),
then `sources` (`{"sources": [...]}`) and `done`. A failure after streaming has started is sent
as an `error` event (`{"detail": "..."}`). The web UI uses this endpoint and falls back to `/api`
if it is unavailable.

> **Deployment note:** on AWS Lambda behind Mangum the response body is buffered, so tokens
> arrive together rather than one by one. True streaming there needs Lambda Web Adapter with
> `AWS_LWA_INVOKE_MODE=RESPONSE_STREAM` and a Function URL in `RESPONSE_STREAM` mode.

**Example — PowerShell**

```powershell
$body = @{ input = "What is the price range of rice?" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:3000/api" -Method Post `
  -ContentType "application/json" -Body $body
```

**Example — curl**

```bash
curl -X POST http://localhost:3000/api \
  -H "Content-Type: application/json" \
  -d '{"input":"What is the price range of rice?"}'
```

**Out-of-scope question**

```json
{ "content": "I don't know from uploaded PDF." }
```

---

## Project structure

```
knowledge base rag/
├── .env                 # secrets - git-ignored, never commit
├── .gitignore
├── .python-version      # pins Python 3.12
├── knowledge.pdf        # sample document (upload more via the UI)
├── ingested.json        # runtime registry: filename -> sha256 + chunk count
├── main.py              # the entire application
├── pyproject.toml       # dependency declarations
├── static/              # CSS + JS for the web UI
├── templates/           # Jinja2 template (index.html)
├── uploads/             # files uploaded through the UI (runtime)
├── uv.lock              # locked dependency versions
└── README.md
```

---

## Configuration

All configuration is read from `.env`:

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEY` | yes | Authenticates requests to the Groq API. |
| `LLM_TIMEOUT_SECONDS` | no | Timeout for each LLM call (default 60 seconds). |
| `SESSION_SECRET` | yes on Lambda, recommended locally | Random string (e.g. `python -c "import secrets;print(secrets.token_hex(32))"`) used to sign the session cookie. Without it locally a new secret is made on every start, so sessions reset on restart. Changing it logs everyone out of their files. |
| `COHERE_API_KEY` | yes | Authenticates embedding and re-ranking requests. |
| `QDRANT_URL` | yes | URL of the Qdrant cluster. |
| `QDRANT_API_KEY` | yes | Authenticates requests to Qdrant Cloud. |

The tunable values are currently hard-coded in `main.py`:

| Setting | Value | Where |
|---|---|---|
| Chunk size / overlap | 1000 / 200 | `ingest_file()` |
| Candidate chunks retrieved | 20 | `similarity_search(..., k=20)` |
| Chunks passed to the LLM | 5 | `rerank(..., top_n=5)` |
| Collection name | `grocery store` | `COLLECTION_NAME` |
| Accepted file types | `.pdf`, `.txt`, `.md`, `.markdown` | `ALLOWED_EXTENSIONS` |
| Duplicate detection | sha256 per filename | `ingested.json` registry |
| Point id namespace | fixed UUID | `PDF_NAMESPACE` - must never change |
| LLM model | `openai/gpt-oss-120b` | `ChatGroq(...)` |

---

## How a request is processed

1. FastAPI validates the JSON body against the `AskRequest` model; a missing `input`
   field returns `422 Unprocessable Entity` automatically.
2. The question is embedded with Cohere `embed-english-v3.0`.
4. Qdrant returns the 20 chunks with the highest cosine similarity.
5. Cohere `rerank-english-v3.0` re-scores those chunks and the best 5 are kept.
6. The 5 chunks are concatenated into a single context string.
7. The system prompt instructs the model to answer **only** from that context, and to
   reply `I don't know from uploaded PDF.` when the answer is absent.
8. The answer is returned as `{ "content": "..." }`.

---

## Ingestion behaviour

Ingestion is **explicit** (upload-driven) and **idempotent**:

- It runs only on `POST /upload` - never on startup, never on `/api` calls.
- Every upload is hashed (`sha256`) and recorded in `ingested.json` (filename -> hash):
  - **Same filename + same hash** (and chunks still present in Qdrant) → **skipped**
    entirely: no re-embedding, no API cost, no duplicates.
  - **Same filename + new hash** → **overwrite**: the file's previous chunks are deleted
    (by point id and by a `metadata.source` payload filter), then fresh chunks are inserted.
  - **New filename** → chunks are inserted as a new source; other documents are untouched.
- Point ids are `uuid5(PDF_NAMESPACE, f"{source}::{chunk_text}")`, so the same chunk always
  maps to the same id and the insert behaves as an **upsert**.
- Chat retrieval searches **all** uploaded sources in the collection at once.

> **Warning:** `PDF_NAMESPACE` must never change. If it does, previously stored points stop
> matching the id-based delete and duplicates will accumulate. (The `source` filter would
> still catch them, provided they carry `metadata.source`.)

---

## Notes and limitations

1. **Deleting `ingested.json` forces re-embedding.** The registry is what makes identical
   re-uploads skip. If it is lost, re-uploading the same file re-embeds it once; the
   overwrite logic still prevents duplicates.
2. **Each file overwrites only itself.** Deletion is scoped by `metadata.source` (the
   filename), so one document's overwrite never removes another's chunks.
3. **No authentication.** `/api` and `/upload` are open — do not expose the service
   publicly as-is.
4. **English-only embedding model.** `embed-english-v3.0` is not suitable for
   non-English documents.
5. **PDF, TXT and MD only.** Other file types are rejected with `400 Bad Request`.
   Uploaded files are stored under `uploads/`.
6. **Only the Groq and Qdrant calls have explicit timeouts**; Cohere and S3 use each SDK's defaults.

