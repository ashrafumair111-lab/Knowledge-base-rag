"""Text chunking + deterministic chunk ids."""

import uuid

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import PDF_NAMESPACE

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
)


def chunk_id(sid: str, source: str, text: str) -> str:
    """Deterministic point id -> re-upload upsert ban jata hai.

    session_id id me hai, warna do tabs ki same file ek hi point par collide karegi.
    """
    return str(uuid.uuid5(PDF_NAMESPACE, f"{sid}::{source}::{text}"))
