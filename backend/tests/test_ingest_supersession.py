"""ID-HU-FE-005: uploading a file with the same name as an existing current
document should auto-mark the old one superseded, with no manual
POST /documents/{id}/supersede call required.

Requires a reachable database (run via `docker compose up`), same as
test_document_resolution.py.
"""
import openpyxl

from app.api.routes_ingest import _supersede_previous_version
from app.ingestion.service import ingest_excel_file

# fake_embeddings and db_session fixtures come from conftest.py


def _make_workbook(tmp_path, name: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FCF"
    ws["A1"] = 1
    file_path = tmp_path / name
    wb.save(file_path)
    return file_path


def test_uploading_same_filename_supersedes_the_previous_current_version(tmp_path, db_session):
    path_v1 = _make_workbook(tmp_path, "budget.xlsx")
    old_doc = ingest_excel_file(db_session, filename="budget.xlsx", file_path=path_v1)

    path_v2 = _make_workbook(tmp_path, "budget_v2_bytes.xlsx")
    new_doc = ingest_excel_file(db_session, filename="budget.xlsx", file_path=path_v2)

    try:
        _supersede_previous_version(db_session, new_doc)

        db_session.refresh(old_doc)
        assert old_doc.is_current is False
        assert old_doc.superseded_by_id == new_doc.id
        assert new_doc.is_current is True
    finally:
        db_session.delete(old_doc)
        db_session.delete(new_doc)
        db_session.commit()


def test_uploading_a_new_filename_supersedes_nothing(tmp_path, db_session):
    path = _make_workbook(tmp_path, "unique_name.xlsx")
    document = ingest_excel_file(db_session, filename="unique_name.xlsx", file_path=path)

    try:
        _supersede_previous_version(db_session, document)

        db_session.refresh(document)
        assert document.is_current is True
        assert document.superseded_by_id is None
    finally:
        db_session.delete(document)
        db_session.commit()


def test_already_superseded_documents_are_not_re_superseded(tmp_path, db_session):
    path_v1 = _make_workbook(tmp_path, "chain.xlsx")
    doc_v1 = ingest_excel_file(db_session, filename="chain.xlsx", file_path=path_v1)

    path_v2 = _make_workbook(tmp_path, "chain_v2_bytes.xlsx")
    doc_v2 = ingest_excel_file(db_session, filename="chain.xlsx", file_path=path_v2)
    _supersede_previous_version(db_session, doc_v2)

    path_v3 = _make_workbook(tmp_path, "chain_v3_bytes.xlsx")
    doc_v3 = ingest_excel_file(db_session, filename="chain.xlsx", file_path=path_v3)

    try:
        _supersede_previous_version(db_session, doc_v3)

        db_session.refresh(doc_v1)
        db_session.refresh(doc_v2)
        # doc_v1 was already superseded by doc_v2 — only the then-current
        # doc_v2 should be superseded by doc_v3, doc_v1 stays untouched.
        assert doc_v1.is_current is False
        assert doc_v1.superseded_by_id == doc_v2.id
        assert doc_v2.is_current is False
        assert doc_v2.superseded_by_id == doc_v3.id
    finally:
        db_session.delete(doc_v1)
        db_session.delete(doc_v2)
        db_session.delete(doc_v3)
        db_session.commit()
