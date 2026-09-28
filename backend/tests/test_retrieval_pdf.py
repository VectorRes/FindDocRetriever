"""Integration test: ingest a PDF into Postgres/pgvector, then query it.

Requires a reachable database (run via `docker compose up`, or point
DATABASE_URL at a local Postgres+pgvector instance). Skips otherwise.
"""
from app.ingestion.service import ingest_pdf_file
from app.retrieval.service import retrieve
from tests.pdf_helpers import make_pdf_bytes

# fake_embeddings and db_session fixtures come from conftest.py


def test_ingest_and_query_round_trip(tmp_path, db_session):
    file_path = tmp_path / "q3_report.pdf"
    file_path.write_bytes(
        make_pdf_bytes(
            [
                "Q3 2024 revenue reached 850000 dollars, up 12 percent year over year.",
                "Operating expenses were held flat while headcount grew in engineering.",
            ]
        )
    )

    document = ingest_pdf_file(db_session, filename="q3_report.pdf", file_path=file_path)
    try:
        assert document.status == "ready"
        assert document.doc_type == "pdf"
        document.confidentiality_tag = "public"
        for chunk in document.chunks:
            chunk.confidentiality_tag = "public"
        db_session.commit()

        results = retrieve(db_session, question="What was Q3 revenue?", top_k=5)

        assert results, "expected at least one retrieved chunk"
        top_hit = next(r for r in results if "revenue" in r.text.lower())
        assert top_hit.document_filename == "q3_report.pdf"
        assert top_hit.page_number == 1
        assert top_hit.sheet_name is None
        assert top_hit.cell_range is None
    finally:
        # Ingestion commits directly (it isn't wrapped in the test's rollback-only
        # session), so without this, ingested rows pile up in Postgres across
        # runs and can contaminate other integration tests' top-k results.
        db_session.delete(document)
        db_session.commit()