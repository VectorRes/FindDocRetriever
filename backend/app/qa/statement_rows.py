"""Statement rows for a chat question: lexical search next to similarity search.

Similarity search ranks passages that *talk about* a line item, so in a
long filing a note ("6.8 Encargo fiduciario ... Total $115,321,854") or a
letter to shareholders ("AWS revenue ... $129 billion") can outrank the
financial statement row that *is* the line item ("Total activo"). This
module finds, in the documents retrieval already picked, the table rows
whose label is named by the question (or by its translation), so the
statement's own line is always among the sources and can be checked.

- A row matches fully when every word of its label is in the question
  ("Total activo" for "¿total de activos…?"), partly when at least half is.
- Only rows whose columns are labelled with periods are used: they can be
  read reliably (which value is which year or quarter).
- Access control and auditing are the same as retrieval's.
"""
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.figures.extraction import _as_sources, has_period_columns, source_row_label
from app.ratios.mapping import _FILLER, _singular, _words
from app.retrieval.service import RetrievedChunk

MAX_ROWS = 6
MAX_FULL_ROWS = 4
MAX_ROWS_PER_DOCUMENT = 3

# Question words that never name a line item (English and Spanish).
_QUESTION_WORDS = {
    "what", "was", "were", "is", "are", "how", "much", "many", "did", "does", "do", "which", "a", "an", "in",
    "on", "at", "to", "by", "as", "or", "its", "their", "our", "it", "with", "from", "that", "this", "there",
    "have", "had", "has", "be", "been", "give", "tell", "me", "show", "value", "amount", "figure", "report",
    "reported", "cual", "cuale", "cuanto", "cuanta", "cuantos", "cuantas", "fue", "fueron", "era", "eran",
    "es", "son", "un", "una", "uno", "en", "al", "por", "para", "o", "su", "sus", "que", "como", "tenia",
    "tuvo", "hay", "valor", "monto", "cifra", "totale", "dame", "dime", "se", "lo", "le", "con",
}


def _content_words(text: str) -> set[str]:
    words = {_singular(word) for word in _words(text)}
    return {word for word in words if word not in _FILLER and word not in _QUESTION_WORDS and len(word) > 1}


@dataclass
class StatementRows:
    rows: list[RetrievedChunk]
    # Rows whose whole label the question names: the line item asked about.
    full_match_ids: set[str]


def statement_rows(
    db: Session, questions: list[str], chunks: list[RetrievedChunk],
    user_roles: str | None, user_id: str | None,
) -> StatementRows:
    """Table rows named by any of `questions` (the question and its
    translation), from the documents `chunks` come from; full matches first."""
    from app.db.models import Chunk, Document

    questions = [question for question in questions if question]  # no translation: None
    asked = set().union(*(_content_words(question) for question in questions))
    document_ids = list(dict.fromkeys(chunk.document_id for chunk in chunks))
    if not asked or not document_ids:
        return StatementRows([], set())

    full: list[tuple[tuple, Chunk]] = []
    partial: list[tuple[tuple, Chunk]] = []
    for chunk in db.scalars(
        select(Chunk).where(Chunk.document_id.in_(document_ids), Chunk.content_type == "table_row")
    ):
        label = source_row_label(chunk.text)
        if not label:
            continue
        # The line item itself, not the section it sits in ("Current assets: Inventories").
        words = _content_words(label.rsplit(":", 1)[-1])
        if not words:
            continue
        shared = words & asked
        if not shared or not has_period_columns(chunk.text):
            continue
        is_total = label.lower().startswith("total")
        if shared == words:
            # More specific labels first ("Total current liabilities" before "Total liabilities").
            full.append(((-len(words), not is_total, chunk.page_number or 0), chunk))
        elif len(shared) * 2 >= len(words) and any(len(word) > 3 for word in shared):
            partial.append(((-len(shared) / len(words), not is_total, chunk.page_number or 0), chunk))

    picked: list[Chunk] = []
    per_document: dict = {}
    for rank, group in ((0, full), (1, partial)):
        for _, chunk in sorted(group, key=lambda item: item[0]):
            if len(picked) >= MAX_ROWS or (rank == 0 and len(picked) >= MAX_FULL_ROWS):
                break
            if per_document.get(chunk.document_id, 0) >= MAX_ROWS_PER_DOCUMENT:
                continue
            per_document[chunk.document_id] = per_document.get(chunk.document_id, 0) + 1
            picked.append(chunk)
    full_ids = {str(chunk.id) for _, chunk in full}

    rows: list[RetrievedChunk] = []
    documents = {doc.id: doc for doc in db.scalars(select(Document).where(Document.id.in_(document_ids)))}
    for document_id in dict.fromkeys(chunk.document_id for chunk in picked):
        mine = [chunk for chunk in picked if chunk.document_id == document_id]
        sources = _as_sources(db, documents[document_id], mine, user_roles, user_id, " | ".join(questions),
                              "Q", MAX_ROWS)
        rows.extend(sources.values())
    order = {str(chunk.id): i for i, chunk in enumerate(picked)}
    rows.sort(key=lambda row: order.get(row.chunk_id, 0))
    return StatementRows(rows, {row.chunk_id for row in rows if row.chunk_id in full_ids})


def row_figures(row: RetrievedChunk) -> set[float]:
    """Every value a statement row gives (all its columns)."""
    from app.qa.confidence import _figures

    body = row.text.split("row '", 1)[-1]
    body = body.split("':", 1)[-1] if "':" in body else body
    values: set[float] = set()
    for _, readings in _figures(re.sub(r"[^;=]+=", " ", body)):
        values |= readings
    return values
