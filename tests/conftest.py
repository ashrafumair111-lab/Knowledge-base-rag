import os

# Dummy env set BEFORE the app is imported, so tests never reach real services
# (load_dotenv does not override variables that are already set).
for key, value in {
    "COHERE_API_KEY": "test",
    "GROQ_API_KEY": "test",
    "LLM_TIMEOUT_SECONDS": "5",
    "QDRANT_URL": "http://localhost:6333",
    "QDRANT_API_KEY": "test",
    "SESSION_SECRET": "test-secret",
}.items():
    os.environ[key] = value

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from fastapi.testclient import TestClient

import src.clients as clients
import src.config as config
import src.db.qdrant as qdrant_db
import src.services.answer_service as answer_service
import src.utils.files as files
from src.app import app


class FakeLLM:
    """Stands in for Groq: records the prompt and answers with plain text."""

    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content="fake answer")

    def stream(self, messages):
        self.calls.append(messages)
        yield AIMessageChunk(content="fake ")
        yield AIMessageChunk(content="answer")


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    """App with every external service faked and data dirs in a temp folder."""
    qdrant = MagicMock()
    qdrant.count.return_value.count = 0
    qdrant.scroll.return_value = ([], None)  # no files in the session
    vector_store = MagicMock()
    cohere_client = MagicMock()
    llm = FakeLLM()

    # Patch where the name is USED (module globals), not the import site:
    # every consumer reads them through `src.db.qdrant` / `src.clients`.
    monkeypatch.setattr(qdrant_db, "qdrant", qdrant)
    monkeypatch.setattr(qdrant_db, "vector_store", vector_store)
    monkeypatch.setattr(qdrant_db, "_collection_ready", True)
    monkeypatch.setattr(clients, "cohere_client", cohere_client)
    monkeypatch.setattr(clients, "llm", llm)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "REGISTRY_PATH", tmp_path / "ingested.json")

    # Handy handle for tests: m.vector_store, m.llm, m.qdrant, ...
    handle = SimpleNamespace(
        app=app,
        qdrant=qdrant,
        vector_store=vector_store,
        cohere_client=cohere_client,
        llm=llm,
        UPLOAD_DIR=config.UPLOAD_DIR,
        REGISTRY_PATH=config.REGISTRY_PATH,
        with_heal=qdrant_db.with_heal,
        safe_filename=files.safe_filename,
        history_note=answer_service.history_note,
        EMBED_BATCH_SIZE=config.EMBED_BATCH_SIZE,
    )
    return handle, TestClient(app)


@pytest.fixture
def live_url(app_env):
    """The real app served by uvicorn on a free port (same fakes as app_env) for browser tests."""
    import socket
    import threading
    import time

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
