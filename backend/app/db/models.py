import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.queued.value)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-fatal ingestion warnings: macros, external links, protected sheets, circular refs.
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    sheets: Mapped[list["Sheet"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    named_ranges: Mapped[list["NamedRange"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


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


class Chunk(Base):
    """A retrievable, embedded unit of text with citation metadata."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cell_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(get_settings().embedding_dimensions))

    document: Mapped["Document"] = relationship(back_populates="chunks")
