"""Shared fixtures for integration tests that touch Postgres/pgvector."""
import pytest

import app.ingestion.service as ingestion_service_module
import app.retrieval.service as retrieval_service_module
from app.config import get_settings
from app.db.session import SessionLocal, engine


class FakeEmbeddingProvider:
    """Deterministic bag-of-words embedding, sized to match the migrated
    vector column, so tests don't need real API/model calls."""

    def __init__(self):
        self.dimensions = get_settings().embedding_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in text.lower().split():
            vector[hash(word) % self.dimensions] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    provider = FakeEmbeddingProvider()
    monkeypatch.setattr(ingestion_service_module, "get_embedding_provider", lambda: provider)
    monkeypatch.setattr(retrieval_service_module, "get_embedding_provider", lambda: provider)


@pytest.fixture
def db_session():
    try:
        with engine.connect():
            pass
    except Exception:
        pytest.skip("Postgres/pgvector not reachable — start it with `docker compose up`")

    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
