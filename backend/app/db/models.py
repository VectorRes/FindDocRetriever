import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class DocumentStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    """A single uploaded workbook (one filename + version)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512))
    # "excel" or "pdf" — determines which parser/chunker produced this document's chunks.
    doc_type: Mapped[str] = mapped_column(String(16), default="excel")
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.queued.value)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-fatal ingestion warnings: macros, external links, protected sheets, circular refs.
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Minimal versioning (ID-HU-FE-002): set explicitly via POST /documents/{id}/supersede —
    # ingestion has no automatic "same filename = new version" detection yet.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    # Minimal confidentiality tagging (ID-HU-FE-002): "public" or "restricted".
    # Placeholder access model until the full access-control/roles story exists —
    # enforced only via the X-User-Role header in routes_documents.py.
    confidentiality_tag: Mapped[str] = mapped_column(String(32), default="public")
    # Path to the original uploaded file on disk, set by routes_ingest.py after
    # ingestion succeeds. Null if the file was never persisted (e.g. ingested
    # before this existed, or ingestion failed before the copy step).
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    sheets: Mapped[list["Sheet"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    named_ranges: Mapped[list["NamedRange"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    pdf_tables: Mapped[list["PdfTable"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    pdf_references: Mapped[list["PdfReference"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Sheet(Base):
    __tablename__ = "sheets"
    __table_args__ = (UniqueConstraint("document_id", "name", name="uq_sheet_document_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    is_empty: Mapped[bool] = mapped_column(Boolean, default=False)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)

    document: Mapped["Document"] = relationship(back_populates="sheets")
    cells: Mapped[list["Cell"]] = relationship(back_populates="sheet", cascade="all, delete-orphan")


class Cell(Base):
    """One cell's value/formula. Merged cells materialize one row per spanned address."""

    __tablename__ = "cells"
    __table_args__ = (UniqueConstraint("sheet_id", "address", name="uq_cell_sheet_address"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"))
    address: Mapped[str] = mapped_column(String(16))
    row: Mapped[int] = mapped_column(Integer)
    column: Mapped[int] = mapped_column(Integer)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ["Assumptions!B5", ...] extracted from the formula, for cross-sheet traceability.
    formula_references: Mapped[list] = mapped_column(JSON, default=list)
    is_merged: Mapped[bool] = mapped_column(Boolean, default=False)
    merged_range: Mapped[str | None] = mapped_column(String(32), nullable=True)

    sheet: Mapped["Sheet"] = relationship(back_populates="cells")


class NamedRange(Base):
    __tablename__ = "named_ranges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cell_range: Mapped[str] = mapped_column(String(128))

    document: Mapped["Document"] = relationship(back_populates="named_ranges")


class PdfTable(Base):
    __tablename__ = "pdf_tables"
    __table_args__ = (UniqueConstraint("document_id", "page_number", "table_index", name="uq_pdf_table_page_index"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    table_index: Mapped[int] = mapped_column(Integer)
    headers: Mapped[list] = mapped_column(JSON, default=list)
    rows: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_label: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str] = mapped_column(Text)

    document: Mapped["Document"] = relationship(back_populates="pdf_tables")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="pdf_table")


class PdfReference(Base):
    __tablename__ = "pdf_references"
    __table_args__ = (UniqueConstraint("document_id", "reference_type", "reference_number", "page_number", name="uq_pdf_reference"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str] = mapped_column(String(16))
    reference_number: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str] = mapped_column(Text)

    document: Mapped["Document"] = relationship(back_populates="pdf_references")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="pdf_reference")


class Chunk(Base):
    """A retrievable, embedded unit of text with citation metadata."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cell_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 1-indexed PDF page this chunk came from; null for Excel chunks.
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(get_settings().embedding_dimensions))
    content_type: Mapped[str] = mapped_column(String(32), default="text")
    pdf_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pdf_tables.id", ondelete="CASCADE"), nullable=True)
    pdf_reference_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pdf_references.id", ondelete="CASCADE"), nullable=True)
    # "native" (extracted directly from the PDF's text layer) or "ocr" (recovered
    # by running OCR on a scanned/image-only page).
    extraction_method: Mapped[str] = mapped_column(String(16), default="native")
    # Confidence in [0, 1]; null for chunk types where confidence isn't meaningful.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    document: Mapped["Document"] = relationship(back_populates="chunks")
    pdf_table: Mapped["PdfTable | None"] = relationship(back_populates="chunks")
    pdf_reference: Mapped["PdfReference | None"] = relationship(back_populates="chunks")


class ConversationSession(Base):
    """A chat session (ID-HU-FE-001): groups the question/answer turns that
    should be kept as context for follow-up questions."""

    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )


class ConversationMessage(Base):
    """One question/answer turn, kept so later turns in the same session can
    be answered with the prior exchange as context."""

    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation_sessions.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    # Flattened statement text, for feeding back as history context — not the full AnswerResponse shape.
    answer_text: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    grounded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped["ConversationSession"] = relationship(back_populates="messages")
