import os

# Dummy env set BEFORE main is imported, so tests never reach real services
# (load_dotenv does not override variables that are already set).
for key, value in {
    "COHERE_API_KEY": "test",
    "GOOGLE_API_KEY": "test",
    "GEMINI_MODEL": "test-model",
    "LLM_TIMEOUT_SECONDS": "5",
    "QDRANT_URL": "http://localhost:6333",
    "QDRANT_API_KEY": "test",
}.items():
    os.environ[key] = value

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from fastapi.testclient import TestClient

import main


class FakeLLM:
    """Stands in for Gemini: records the prompt and answers with Gemini-3-shaped blocks."""

    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return AIMessage(
            content=[
                {"type": "thinking", "thinking": "private reasoning"},
                {"type": "text", "text": "fake answer"},
            ]
        )


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    """main with every external service faked and data dirs in a temp folder."""
    qdrant = MagicMock()
    qdrant.count.return_value.count = 0
    monkeypatch.setattr(main, "qdrant", qdrant)
    monkeypatch.setattr(main, "vector_store", MagicMock())
    monkeypatch.setattr(main, "cohere_client", MagicMock())
    monkeypatch.setattr(main, "llm", FakeLLM())
    monkeypatch.setattr(main, "_collection_ready", True)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "REGISTRY_PATH", tmp_path / "ingested.json")
    return main, TestClient(main.app)
