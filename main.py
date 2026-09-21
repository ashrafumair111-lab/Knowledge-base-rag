import asyncio
import os
from contextlib import asynccontextmanager

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

load_dotenv()

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

# Dense vector store (Qdrant) - ye SEMANTIC search ka engine hai
vector_store = QdrantVectorStore.from_existing_collection(
    embedding=embeddings,
    url=os.environ["QDRANT_URL"],
    api_key=os.environ["QDRANT_API_KEY"],
    collection_name="grocery store",
)


def upload() -> None:
    """PDF padho -> text nikalo -> chunks banao -> Qdrant mein save karo."""
    pdf_path = "./knowledge.pdf"
    reader = PdfReader(pdf_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )

    docs = splitter.create_documents([text])

    # Dense vector store (Qdrant) mein save - SEMANTIC search ke liye
    vector_store.add_documents(docs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Server start hone se pehle PDF ingest karo (jaise index.js mein upload() tha)
    await asyncio.to_thread(upload)
    yield


app = FastAPI(lifespan=lifespan)


class AskRequest(BaseModel):
    input: str


@app.post("/api")
def ask_grok(body: AskRequest) -> dict:
    question = body.input

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
