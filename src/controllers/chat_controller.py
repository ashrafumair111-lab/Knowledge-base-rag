"""Chat controllers: normal JSON answer + SSE stream."""

from fastapi.responses import StreamingResponse

from src.models.chat import AskRequest
from src.services import answer_service


def ask_grok(body: AskRequest, sid: str) -> dict:
    return answer_service.answer(body, sid)


def ask_stream(body: AskRequest, sid: str) -> StreamingResponse:
    # Retrieval pehle (normal HTTP errors yahin se), phir sirf LLM tokens stream hote hain
    messages, sources = answer_service.retrieve(body, sid)
    return StreamingResponse(
        answer_service.stream_events(messages, sources),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
