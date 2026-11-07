"""Answer generation: prompt building, quota messages aur SSE streaming."""

import json
import logging
import re

from fastapi import HTTPException
from langchain_core.exceptions import ModelRateLimitError

from src import clients
from src.models.chat import AskRequest
from src.services.retrieval_service import find_docs, rerank

logger = logging.getLogger("uvicorn.error")  # uvicorn ke saath wahi console/log


def history_note(history: list | None) -> str:
    """Chat history ko prompt ke liye chhota text banao (follow-ups jaise 'explain it')."""
    turns = [
        m for m in (history or [])
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
    ]
    if not turns:
        return ""
    lines = [
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
        for m in turns[-6:]
    ]
    return "Recent conversation (use it to resolve follow-ups like 'explain it'): " + " | ".join(lines) + " "


def retrieve(body: AskRequest, sid: str) -> tuple[list[dict], list[str]]:
    """Search + rerank (sirf is session ke chunks) -> (LLM messages, citation sources)."""
    question = body.input

    # --- STEP 1: DENSE search - SIRF isi session ke chunks ---
    dense_docs = find_docs(question, sid)
    sources: list[str] = []

    if dense_docs:
        context, sources, file_note = rerank(question, dense_docs)
    else:
        # Is session me abhi koi file nahi - general knowledge se jawab do
        context = "(no document context available)"
        file_note = "No files have been uploaded in this session yet. "

    messages = [
        {
            "role": "system",
            "content": (
                "You are a friendly AI chat assistant with a knowledge base of the user's "
                f"uploaded files. {file_note}RULES: "
                "- If the question is about those files, answer from the context below and "
                "ground your answer in it. "
                "- Otherwise answer normally from your own knowledge - general facts, "
                "chit-chat, greetings, coding, advice, anything. "
                "- Never refuse or say you can't answer just because the answer isn't in the context. "
                "- Always write your answer in English, formatted as Markdown (short paragraphs, "
                "lists and **bold** where they help readability). "
                '- Only for questions specifically about the uploaded files where the context '
                'has no answer, say: "I don\'t know from uploaded PDF." '
                f"{history_note(body.history)}Context:\n{context}"
            ),
        },
        {"role": "user", "content": question},
    ]
    return messages, sources


def quota_detail(exc: Exception) -> str:
    """429 se user ko saaf wajah + intezaar ka waqt.

    Groq: "Please try again in 18.5s". Gemini: "... retry in 10h6m19.2s".
    """
    wait = re.search(
        r"(?:retry|try again) in ((?:\d+(?:\.\d+)?h)?(?:\d+(?:\.\d+)?m)?(?:\d+(?:\.\d+)?s)?)", str(exc)
    )
    parts = re.findall(r"\d+(?:\.\d+)?[hms]", wait.group(1)) if wait else []
    when = f" in about {' '.join(parts)}" if parts else " later"
    return f"The AI model's request quota is used up for now. Please try again{when}."


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def answer(body: AskRequest, sid: str) -> dict:
    """Non-streaming answer (POST /api)."""
    messages, sources = retrieve(body, sid)
    try:
        response = clients.llm.invoke(messages)
    except ModelRateLimitError as exc:
        logger.warning("LLM quota/rate limit hit: %s", str(exc)[:200])
        raise HTTPException(status_code=429, detail=quota_detail(exc)) from exc

    # .text -> sirf jawab ka text (Groq plain string bhejta hai, Gemini 3 blocks bhejta tha)
    return {"content": response.text, "sources": sources}


def stream_events(messages: list[dict], sources: list[str]):
    """SSE events: token* -> sources -> done (errors travel as an 'error' event)."""
    sent = False
    try:
        for chunk in clients.llm.stream(messages):
            if chunk.text:  # .text = sirf answer text
                sent = True
                yield sse("token", {"text": chunk.text})
    except ModelRateLimitError as exc:
        # Quota/rate limit: dobara try karna bekar hai - saaf wajah batao
        logger.warning("LLM quota/rate limit hit: %s", str(exc)[:200])
        yield sse("error", {"detail": quota_detail(exc)})
        return
    except Exception:
        logger.exception("LLM stream failed (after %s text)", "some" if sent else "no")
        if sent:  # aadha jawab ja chuka - dobara bhejne se text dohra jayega
            yield sse("error", {"detail": "Answer generation failed. Please try again."})
            return
        # Kuch bhi nahi gaya: transient LLM error (overload/rate limit). invoke() ke apne
        # retries hain - ek baar normal tareeqe se poora jawab try karo.
        try:
            yield sse("token", {"text": clients.llm.invoke(messages).text})
        except Exception:
            logger.exception("LLM fallback invoke failed")
            yield sse("error", {"detail": "Answer generation failed. Please try again."})
            return
    yield sse("sources", {"sources": sources})
    yield sse("done", {})
