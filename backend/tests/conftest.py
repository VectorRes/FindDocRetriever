"""Shared fixtures for integration tests that touch Postgres/pgvector.

Integration tests run against their own database, never the development one:
documents uploaded while using the app would otherwise crowd the test's own
chunks out of retrieval's top-k (and tests would write into real data). The
test database is `TEST_DATABASE_URL` if set, else DATABASE_URL's database with
a `_test` suffix (findoc_rag -> findoc_rag_test). It's created if missing,
migrated to head and emptied at the start of every run.

This must happen before anything imports app.db.session, which creates the
engine from the settings at import time.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import get_settings


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    url = make_url(get_settings().database_url)
    if not url.database.endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    return url.render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# No real model calls from tests: the translation step is tested on its own.
os.environ["QUERY_TRANSLATION"] = "false"
get_settings.cache_clear()

import app.ingestion.service as ingestion_service_module  # noqa: E402
import app.retrieval.service as retrieval_service_module  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _prepare_test_database() -> bool:
    """Create, migrate and empty the test database. False if Postgres isn't reachable."""
    url = make_url(TEST_DATABASE_URL)
    server = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with server.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception:
        return False
    finally:
        server.dispose()

    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "app" / "db" / "migrations"))
    command.upgrade(config, "head")  # env.py reads the (now test) DATABASE_URL

    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    return True


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Runs once per session; tests without a database still run if it's unreachable."""
    return _prepare_test_database()


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
