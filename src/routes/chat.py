"""Chat endpoints: JSON answer aur SSE stream."""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.auth.session import session_id
from src.controllers import chat_controller
from src.models.chat import AskRequest

router = APIRouter()


@router.post("/api")
def ask_grok(body: AskRequest, sid: str = Depends(session_id)) -> dict:
    return chat_controller.ask_grok(body, sid)


@router.post("/api/stream")
def ask_stream(body: AskRequest, sid: str = Depends(session_id)) -> StreamingResponse:
    return chat_controller.ask_stream(body, sid)
