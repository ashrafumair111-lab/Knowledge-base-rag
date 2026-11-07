"""Shared external clients: Cohere (embeddings + rerank) aur Groq (LLM).

Sirf yahan banti hain - baaki modules inhe ``from src import clients`` ke through
padhte hain, taake tests ek hi jagah (yahan) patch kar saken.
"""

import os

import cohere
from langchain_cohere import CohereEmbeddings
from langchain_groq import ChatGroq

from src.config import GROQ_MODEL

# Cohere client - reranking (query ke against chunks ko dobara score karne) ke liye
cohere_client = cohere.Client(api_key=os.environ["COHERE_API_KEY"])

# Answer generate karne wala LLM (Groq) - GROQ_API_KEY env se uthata hai.
llm = ChatGroq(
    model=GROQ_MODEL,
    temperature=0,
    max_retries=2,
    timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
)

# Embeddings - semantic/meaning-based search ke liye (1024 dims)
embeddings = CohereEmbeddings(model="embed-english-v3.0")
