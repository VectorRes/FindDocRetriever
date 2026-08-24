"""Integration test: ingest a workbook into Postgres/pgvector, then query it.

Requires a reachable database (run via `docker compose up`, or point
DATABASE_URL at a local Postgres+pgvector instance). Skips otherwise.
"""
import openpyxl
import pytest

import app.ingestion.service as ingestion_service_module
import app.retrieval.service as retrieval_service_module
from app.config import get_settings
from app.db.session import SessionLocal, engine
from app.ingestion.service import ingest_excel_file
from app.retrieval.service import retrieve


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


def test_ingest_and_query_round_trip(tmp_path, db_session):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws["A1"] = "Line Item"
    ws["B1"] = "Q1"
    ws["A2"] = "Revenue"
    ws["B2"] = 120000
    ws["A3"] = "COGS"
    ws["B3"] = 45000
    file_path = tmp_path / "budget.xlsx"
    wb.save(file_path)

    document = ingest_excel_file(db_session, filename="budget.xlsx", file_path=file_path)
    assert document.status == "ready"

    results = retrieve(db_session, question="What is the Revenue line item?", top_k=5)

    assert results, "expected at least one retrieved chunk"
    assert any("Revenue" in r.text for r in results)
    assert results[0].document_filename == "budget.xlsx"
    top_hit = next(r for r in results if "Revenue" in r.text)
    assert top_hit.sheet_name == "Budget"
    assert top_hit.cell_range == "A2:B2"
