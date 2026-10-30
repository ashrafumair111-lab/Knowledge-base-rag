import os

# Dummy env set BEFORE main is imported, so tests never reach real services
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

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from fastapi.testclient import TestClient

import main


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
    """main with every external service faked and data dirs in a temp folder."""
    qdrant = MagicMock()
    qdrant.count.return_value.count = 0
    qdrant.scroll.return_value = ([], None)  # no files in the session
    monkeypatch.setattr(main, "qdrant", qdrant)
    monkeypatch.setattr(main, "vector_store", MagicMock())
    monkeypatch.setattr(main, "cohere_client", MagicMock())
    monkeypatch.setattr(main, "llm", FakeLLM())
    monkeypatch.setattr(main, "_collection_ready", True)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "REGISTRY_PATH", tmp_path / "ingested.json")
    return main, TestClient(main.app)


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

    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
