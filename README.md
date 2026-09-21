# RAG Python (FastAPI)

Ye `D:\ai understanding\rag` (Node.js + Express) ka Python port hai — **bilkul same RAG pipeline**,
sirf **hybrid search hata di gayi hai** (ab sirf **semantic search** hai).

## Stack

| Layer | Node (`index.js`) | Python (`main.py`) |
|---|---|---|
| Web server | Express | FastAPI + Uvicorn |
| Embeddings | `@langchain/cohere` `embed-english-v3.0` | `langchain-cohere` `embed-english-v3.0` |
| Vector store | `@langchain/qdrant` | `langchain-qdrant` |
| Collection | `grocery store` | `grocery store` (same) |
| PDF parse | `pdf-parse` | `pypdf` |
| Splitter | `RecursiveCharacterTextSplitter` 1000/200 | same 1000/200 |
| LLM | `@langchain/groq` `openai/gpt-oss-120b`, temp 0 | `langchain-groq`, same model & temp |
| Rerank | `cohere-ai` `rerank-english-v3.0`, topN 5 | `cohere` SDK, same model & topN |
| Port | 3000 | 3000 |
| Search | dense + BM25 (hybrid) | **dense only (semantic)** |

## Setup (uv, Python 3.12)

```powershell
cd 'D:\ai understanding\rag python'
uv sync
```

## Run

```powershell
uv run main.py
# ya development ke liye reload ke sath:
uv run uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

## API

Same endpoint aur same request/response shape jo Node version mein tha:

```powershell
curl -X POST http://localhost:3000/api `
  -H "Content-Type: application/json" `
  -d '{"input":"What is the price range of rice?"}'
```

Response:

```json
{ "content": "..." }
```

## Query flow (semantic only)

1. **Semantic search** — `vector_store.similarity_search(question, k=20)`
2. **Rerank** — Cohere `rerank-english-v3.0` -> top 5
3. **LLM** — Groq (`openai/gpt-oss-120b`) strict prompt ke sath, sirf context se answer

> Hybrid search ke steps (BM25 index, `reciprocalRankFusion`) jaan-boojh kar remove kar diye gaye hain.

## .env

Same `.env` jo Node project mein tha, is folder mein copy kar diya gaya hai:

```
GROQ_API_KEY=...
COHERE_API_KEY=...
QDRANT_URL=...
QDRANT_API_KEY=...
```

## Note / caveat

`main.py` server start hone par `upload()` chalata hai (bilkul jaise `index.js` karta tha),
yani har restart par PDF ke chunks **dobara** Qdrant collection `grocery store` mein add ho jate hain.
Zaroorat ho to `lifespan` se `upload()` hata dein ya use ek flag/if-condition ke peeche rakh dein.

