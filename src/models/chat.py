"""Chat request schema."""

from pydantic import BaseModel


class AskRequest(BaseModel):
    input: str
    history: list[dict] | None = None
