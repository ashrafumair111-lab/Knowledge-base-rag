"""Embedding pacing - Cohere ke tokens/minute budget ke andar rehne ke liye."""

import time

from src.config import CHARS_PER_TOKEN, EMBED_TOKENS_PER_MINUTE


def _pace(batch_chars: int) -> None:
    """Batch ke tokens ke hisaab se wait karo (tokens/minute budget ke andar rehne ke liye)."""
    tokens = batch_chars / CHARS_PER_TOKEN
    time.sleep(tokens / EMBED_TOKENS_PER_MINUTE * 60)
