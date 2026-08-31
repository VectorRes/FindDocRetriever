"""Integration test: ingest a workbook into Postgres/pgvector, then query it.

Requires a reachable database (run via `docker compose up`, or point
DATABASE_URL at a local Postgres+pgvector instance). Skips otherwise.
"""
import openpyxl

from app.ingestion.service import ingest_excel_file
from app.retrieval.service import retrieve

# fake_embeddings and db_session fixtures come from conftest.py


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
