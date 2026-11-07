"""File validation + hashing helpers."""

import hashlib
from pathlib import Path

from fastapi import HTTPException

from src.config import ALLOWED_EXTENSIONS


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str) -> str:
    """Base filename + extension check (ek saath path-traversal aur type validation)."""
    filename = Path(name or "").name
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Only PDF, TXT and MD files are supported"
        )
    return filename
