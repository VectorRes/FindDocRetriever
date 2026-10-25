"""Chunks are embedded with their document's name (app/ingestion/context.py),
so a question naming the company finds rows that don't say whose they are.

Fake embeddings (bag of words, conftest.py) make "similar" mean "shares
words", which is exactly what the document name adds.
"""
import random
import string
import uuid

import openpyxl
from sqlalchemy import select

import app.ingestion.service as ingestion_service
from app.db.models import Chunk, Document
from app.ingestion.context import document_label, embedding_text
from app.ingestion.reembed import reembed
from app.ingestion.service import ingest_excel_file
from app.qa.query_expansion import retrieve_for_question


def test_document_label_and_embedding_text():
    assert document_label("Micron-Technology-Inc.-MU-Q3-2026-Report.pdf") == "Micron Technology Inc. MU Q3 2026 Report"
    assert document_label("EERR_Andina_2024.xlsx") == "EERR Andina 2024"
    text = "Financial table page 4, row 'Revenue': column 1=9,301"
    assert embedding_text("Micron-Q3.pdf", text) == f"Document: Micron Q3\n{text}"


class RecordingProvider:
    def __init__(self, inner):
        self.inner, self.texts = inner, []
        self.dimensions = inner.dimensions

    def embed(self, texts):
        self.texts.extend(texts)
        return self.inner.embed(texts)


def _workbook(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    path = tmp_path / f"{uuid.uuid4().hex}.xlsx"
    wb.save(path)
    return path


def test_ingestion_embeds_with_the_document_name_but_stores_the_text_as_is(db_session, tmp_path, monkeypatch):
    provider = RecordingProvider(ingestion_service.get_embedding_provider())
    monkeypatch.setattr(ingestion_service, "get_embedding_provider", lambda: provider)
    name = f"Nimbus-{uuid.uuid4().hex[:6]}-Q3-Report.xlsx"
    document = ingest_excel_file(db_session, filename=name,
                                 file_path=_workbook(tmp_path, [["Concepto", "Q3"], ["Revenue", 9301]]))
    try:
        assert provider.texts and all(t.startswith(f"Document: {document_label(name)}\n") for t in provider.texts)
        stored = db_session.scalars(select(Chunk.text).where(Chunk.document_id == document.id)).all()
        assert stored and not any(t.startswith("Document:") for t in stored)
    finally:
        db_session.delete(document)
        db_session.commit()


def test_main_a_question_naming_the_company_finds_its_unlabelled_rows(db_session, tmp_path):
    """The Micron case: its revenue row doesn't name the company, another
    company's "Total revenues" row matches the question's words; the Micron
    row must still be among the sources, first."""
    company = "".join(random.choices(string.ascii_lowercase, k=8))
    other = "".join(random.choices(string.ascii_lowercase, k=8))
    docs = [
        ingest_excel_file(db_session, filename=f"{other.title()}-10K-2025.xlsx", file_path=_workbook(tmp_path, [
            [f"{other.title()} Consolidated Statements of Operations"], ["Concepto", "2025"],
            ["Total revenues", 94827], ["Total automotive revenues", 69526], ["Total cost of revenues", 77733],
        ])),
        ingest_excel_file(db_session, filename=f"{company.title()}-Technology-Inc.-Q3-2026-Report.xlsx",
                          file_path=_workbook(tmp_path, [["Concepto", "column 1"], ["Revenue", 9301]])),
    ]
    for document in docs:
        document.confidentiality_tag = "internal"
        for chunk in document.chunks:
            chunk.confidentiality_tag = "internal"
    db_session.commit()
    try:
        # (The fake embeddings split on spaces: no "?" glued to the name.)
        chunks = retrieve_for_question(db_session, f"What were the total revenues of {company} ?", top_k=3,
                                       user_roles="analyst", user_id="context-test")
        ours = [c for c in chunks if c.document_id == str(docs[1].id)]
        assert chunks[0].document_id == str(docs[1].id)  # the named company's document comes first
        assert any("Revenue" in c.text for c in ours)  # with its revenue row
    finally:
        for document in docs:
            db_session.delete(document)
        db_session.commit()


def test_reembed_updates_existing_chunks(db_session, tmp_path):
    name = f"Orbit-{uuid.uuid4().hex[:6]}.xlsx"
    document = ingest_excel_file(db_session, filename=name, file_path=_workbook(tmp_path, [["Revenue", 1]]))
    chunk = db_session.scalars(select(Chunk).where(Chunk.document_id == document.id)).first()
    chunk.embedding = [0.0] * len(chunk.embedding)  # as if embedded before
    db_session.commit()
    seen = []

    class Provider:
        def embed(self, texts):
            seen.extend(texts)
            return [[1.0] + [0.0] * (len(chunk.embedding) - 1) for _ in texts]

    try:
        assert reembed(db_session, Provider()) >= 1
        db_session.refresh(chunk)
        assert chunk.embedding[0] == 1.0
        assert f"Document: {document_label(name)}\n{chunk.text}" in seen
    finally:
        db_session.delete(db_session.get(Document, document.id))
        db_session.commit()
