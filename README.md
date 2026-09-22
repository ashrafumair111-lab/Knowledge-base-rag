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

1. **On the first request** to `POST /api`, the service reads `knowledge.pdf`, splits it into
   overlapping text chunks, embeds them with Cohere, and **overwrites** the vectors held in
   the Qdrant collection. Later requests in the same process skip this step entirely.
2. **On every request** it embeds the question, retrieves the 20 most semantically similar
   chunks, re-ranks them with Cohere, and keeps the best 5.
3. A Groq-hosted LLM generates the final answer using **only** that retrieved context.
   If the answer is not present in the document, it replies:
   `I don't know from uploaded PDF.`

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
      -> Groq openai/gpt-oss-120b       temperature=0, strict prompt
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

### 3. Install dependencies

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

`Application startup complete.` confirms that the PDF was read, chunked and stored
successfully. The first start after a fresh install can take around 30 seconds while
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

### `POST /api`

Answers a question using the ingested PDF as the only source of truth.

**Request**

```json
{ "input": "What is the price range of rice?" }
```

**Response — `200 OK`**

```json
{ "content": "The catalog lists two types of rice: Basmati rice (1 kg) at Rs 220-400 and IRRI rice (1 kg) at Rs 130-220." }
```

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
├── knowledge.pdf        # the document that gets ingested
├── main.py              # the entire application
├── pyproject.toml       # dependency declarations
├── uv.lock              # locked dependency versions
└── README.md
```

---

## Configuration

All configuration is read from `.env`:

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEY` | yes | Authenticates requests to the Groq LLM API. |
| `COHERE_API_KEY` | yes | Authenticates embedding and re-ranking requests. |
| `QDRANT_URL` | yes | URL of the Qdrant cluster. |
| `QDRANT_API_KEY` | yes | Authenticates requests to Qdrant Cloud. |

The tunable values are currently hard-coded in `main.py`:

| Setting | Value | Where |
|---|---|---|
| Chunk size / overlap | 1000 / 200 | `upload()` |
| Candidate chunks retrieved | 20 | `similarity_search(..., k=20)` |
| Chunks passed to the LLM | 5 | `rerank(..., top_n=5)` |
| Collection name | `grocery store` | `COLLECTION_NAME` |
| Source document | `./knowledge.pdf` | `PDF_PATH` |
| Point id namespace | fixed UUID | `PDF_NAMESPACE` - must never change |
| LLM model | `openai/gpt-oss-120b` | `ChatGroq(...)` |

---

## How a request is processed

1. FastAPI validates the JSON body against the `AskRequest` model; a missing `input`
   field returns `422 Unprocessable Entity` automatically.
2. `ensure_ingested()` performs the overwrite described in
   [Ingestion behaviour](#ingestion-behaviour) - only on the first request in a process.
3. The question is embedded with Cohere `embed-english-v3.0`.
4. Qdrant returns the 20 chunks with the highest cosine similarity.
5. Cohere `rerank-english-v3.0` re-scores those chunks and the best 5 are kept.
6. The 5 chunks are concatenated into a single context string.
7. The system prompt instructs the model to answer **only** from that context, and to
   reply `I don't know from uploaded PDF.` when the answer is absent.
8. The answer is returned as `{ "content": "..." }`.

---

## Ingestion behaviour

Ingestion is **lazy** and **idempotent**:

- It runs on the **first `POST /api` call of each process** - never on startup. Later calls
  in the same process do nothing (`ensure_ingested()` short-circuits on a flag).
- It **overwrites** instead of appending. Before inserting, this document's previous chunks
  are deleted:
  1. by point id (deterministic content ids), and
  2. by a payload filter on `metadata.source`, which also catches chunks whose text changed.
- Point ids are `uuid5(PDF_NAMESPACE, f"{PDF_PATH}::{chunk_text}")`, so the same chunk always
  maps to the same id and the insert behaves as an **upsert**.
- The collection therefore always holds exactly **7 points**, no matter how often the server
  restarts.

> **Warning:** `PDF_NAMESPACE` must never change. If it does, previously stored points stop
> matching the id-based delete and duplicates will accumulate. (The `source` filter would
> still catch them, provided they carry `metadata.source`.)

---

## Notes and limitations

1. **Every restart rewrites the vectors once.** Because ingestion overwrites, the first
   `/api` call after each start re-embeds the 7 chunks (~2-4 seconds plus a small Cohere
   cost). That rewrite is what guarantees no duplicates. To skip it when the PDF is
   unchanged, compare a `sha256` of the file before calling `upload()`.
2. **The collection must not be shared between documents.** The `source` filter deletes by
   document. If you add a second PDF, ingest it with its own `PDF_PATH`/source value so one
   document's overwrite never removes another's chunks.
3. **No authentication.** `POST /api` is open — do not expose the service publicly as-is.
4. **English-only embedding model.** `embed-english-v3.0` is not suitable for
   non-English documents.
5. **Single document.** `PDF_PATH` points at `./knowledge.pdf`, so the service must be
   started from the project root.
6. **No custom retry or timeout policy** beyond each SDK's defaults.

