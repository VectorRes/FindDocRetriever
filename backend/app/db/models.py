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
    # ID-HU-INT-002: deleted from the repository it was synced from; kept for
    # audit but never searched again (not even by explicit document id).
    removed = "removed"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_version_group(context) -> str:
    # Imported lazily: app.versioning imports this module.
    from app.versioning import derive_version_group

    params = context.get_current_parameters()
    return derive_version_group(params["filename"], params.get("doc_type") or "excel")


def _default_source_tier(context) -> str:
    from app.source_tiers import infer_tier

    return infer_tier(context.get_current_parameters()["filename"])


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

    # Version control (ID-HU-BE-015) — maintained by app/versioning.py.
    # `is_current` marks the group's default version (latest approved, else
    # latest); it's what QA retrieval uses. `superseded_by_id` links an older
    # version to the one that replaced it.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    # Family of uploads that are versions of the same logical document,
    # derived from the filename ("Budget_v1.xlsx" / "Budget_v2.xlsx" -> "excel:budget").
    version_group: Mapped[str] = mapped_column(
        String(512), index=True, default=_default_version_group
    )
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    # "draft" until approved by a reviewer; see app/versioning.py.
    approval_status: Mapped[str] = mapped_column(String(16), default="draft")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ID-HU-BE-016: place in the source-of-truth hierarchy, "erp_epm" >
    # "official_report" > "internal_report" (app/source_tiers.py). Inferred
    # from the filename on upload; a reviewer can change it.
    source_tier: Mapped[str] = mapped_column(String(32), default=_default_source_tier)
    # Restrictive by default. Reviewers can classify documents as public/internal
    # or assign a category such as restricted:compensation.
    confidentiality_tag: Mapped[str] = mapped_column(
        String(64), default="restricted", nullable=False
    )
    # Path to the original uploaded file on disk, set by routes_ingest.py after
    # ingestion succeeds. Null if the file was never persisted (e.g. ingested
    # before this existed, or ingestion failed before the copy step).
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    sheets: Mapped[list["Sheet"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    named_ranges: Mapped[list["NamedRange"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    pdf_tables: Mapped[list["PdfTable"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    pdf_references: Mapped[list["PdfReference"]] = relationship(back_populates="document", cascade="all, delete-orphan")

    @property
    def sheet_names(self) -> list[str]:
        """Sheets detected during ingestion (ID-HU-FE-005's parsing summary)."""
        return [sheet.name for sheet in self.sheets]


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
    # Section-level confidentiality. Missing labels are safe by default.
    confidentiality_tag: Mapped[str] = mapped_column(
        String(64), default="restricted", nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")
    pdf_table: Mapped["PdfTable | None"] = relationship(back_populates="chunks")
    pdf_reference: Mapped["PdfReference | None"] = relationship(back_populates="chunks")


class RetrievalAuditLog(Base):
    """Immutable audit event for an access attempt (written by app/audit.py).

    Both granted and denied attempts are logged (ID-HU-BE-013/014): content
    returned by retrieval, and content or documents the user's roles didn't
    allow, so confidentiality controls are demonstrably enforced.
    """

    __tablename__ = "retrieval_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user_id: Mapped[str] = mapped_column(String(255), default="anonymous", nullable=False)
    roles: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # What was attempted: "retrieve" (search), "lookup" (a statement row read
    # directly), "resolve" / "view_cell" / "view_file" (a cited document), "extract"
    # (a figure for ratios/compare), "audit_query".
    action: Mapped[str] = mapped_column(String(32), default="retrieve", nullable=False)
    # Kept as reference IDs rather than foreign keys so audit history survives
    # document/chunk deletion. No chunk for whole-document attempts.
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # The filename at the time, so the trail can be filtered by name after deletion.
    document_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confidentiality_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_insider: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where the attempt came from: chat, query, ratios, compare, resolve, cells, file, audit…
    resource: Mapped[str] = mapped_column(String(32), default="retrieve", nullable=False)
    # Groups the events of one API request.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


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


class DashboardRun(Base):
    """One calculation of an entity's ratios for one period (ID-HU-FE-004).

    The dashboard reads these instead of recalculating (each calculation costs
    model calls); a refresh replaces the run of that entity and period. A run
    is stale when the documents it used are no longer the entity's current
    documents for the period (a new version was approved, one was uploaded or
    deleted)."""

    __tablename__ = "dashboard_runs"
    __table_args__ = (UniqueConstraint("entity_key", "period", name="uq_dashboard_run_entity_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    entity: Mapped[str] = mapped_column(String(255))
    # Normalized entity name ("Andina S.A.S." and "Andina" -> "andina").
    entity_key: Mapped[str] = mapped_column(String(255), index=True)
    period: Mapped[str] = mapped_column(String(64))
    # Chronological order of periods ("2024" -> "2024-5", "Q3 2026" -> "2026-3").
    period_sort: Mapped[str] = mapped_column(String(16))
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    computed_by: Mapped[str] = mapped_column(String(255), default="anonymous")

    snapshots: Mapped[list["RatioSnapshot"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RatioSnapshot.position"
    )


class RatioSnapshot(Base):
    """One ratio of a DashboardRun, as the ratio engine (BE-010) returned it,
    with its inputs and citations so the dashboard can show sources without
    extracting the figures again."""

    __tablename__ = "ratio_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dashboard_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    ratio_key: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    # "calculated" | "needs_review" | "not_calculable"
    status: Mapped[str] = mapped_column(String(32))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    inputs: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    cross_document: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    # Statements the ratio needs that the entity has no document for in this
    # period ("balance_sheet"): shown as "insufficient data".
    missing_statements: Mapped[list] = mapped_column(JSON, default=list)
    # The inputs, by name, that weren't found for this period: what makes a
    # "not_calculable" ratio so (ID-HU-BE-011).
    missing_inputs: Mapped[list] = mapped_column(JSON, default=list)

    run: Mapped["DashboardRun"] = relationship(back_populates="snapshots")


class RepositoryAccount(Base):
    """A Google or Microsoft account a reviewer signed in with (OAuth) to
    connect folders without service credentials (ID-HU-INT-001). Folders
    connected through it are synced with its access. Tokens are encrypted at
    rest (TOKEN_ENCRYPTION_KEY, app/sync/oauth.py); the access token is
    renewed from the refresh token when it expires."""

    __tablename__ = "repository_accounts"
    __table_args__ = (UniqueConstraint("provider", "email", name="uq_repository_account_provider_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(32))  # "google" | "microsoft"
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    refresh_token: Mapped[str] = mapped_column(Text)  # encrypted
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str] = mapped_column(Text, default="")
    connected_by: Mapped[str] = mapped_column(String(255), default="anonymous")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SyncSource(Base):
    """A connected repository folder that is synced automatically (ID-HU-INT-001):
    a SharePoint document-library folder or a Google Drive folder.

    Credentials aren't stored here: they come from the environment
    (SHAREPOINT_*, GOOGLE_SERVICE_ACCOUNT_*). `config` says which folder:
      - sharepoint:   {"site_url", "library", "folder_path"}
      - google_drive: {"folder_id"}
    plus {"include_subfolders": bool} for both. New and updated files are
    ingested with this source's classification, tier and approval settings."""

    __tablename__ = "sync_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32))  # "sharepoint" | "google_drive"
    name: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Applied to every document ingested from this folder.
    confidentiality_tag: Mapped[str] = mapped_column(String(64), default="internal")
    # ID-HU-BE-016 tier; None = inferred from the file name.
    source_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Approve each synced version (the folder only holds approved documents),
    # so an update replaces the previous approved version right away.
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "ok" | "error" | None (never synced), and why it failed.
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Counts of the last run: {"new", "updated", "unchanged", "failed", "skipped",
    # "permissions_changed", "removed", "restored"}.
    last_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # ID-HU-INT-002: how the repository's sharing maps to FinDoc's access
    # model, e.g. [{"principal": "Finance", "tag": "internal"},
    # {"principal": "Legal Corporate", "tag": "restricted:legal"}]. A principal
    # is a group or user (name or email) or "anyone" / "organization" /
    # "domain:<domain>". Empty: permissions aren't propagated and every
    # document gets `confidentiality_tag` (app/sync/permissions.py).
    permission_rules: Mapped[list] = mapped_column(JSON, default=list)

    # Set when the folder was connected through a signed-in account (OAuth):
    # synced with that account's access instead of service credentials.
    # Disconnecting the account disconnects its folders (documents are kept).
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repository_accounts.id", ondelete="CASCADE"), nullable=True
    )

    files: Mapped[list["SyncedFile"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    account: Mapped["RepositoryAccount | None"] = relationship()


class SyncedFile(Base):
    """One file of a SyncSource as last seen, and the document it became.
    A file is re-ingested when its version tag changes (SharePoint cTag,
    Google Drive md5 checksum / modified time)."""

    __tablename__ = "synced_files"
    __table_args__ = (UniqueConstraint("source_id", "remote_id", name="uq_synced_file_source_remote"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sync_sources.id", ondelete="CASCADE"), index=True)
    remote_id: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)
    version_tag: Mapped[str] = mapped_column(String(255))
    remote_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    # "ingested" | "failed" (retried per the retry policy) | "needs_review"
    # (retries exhausted, ID-HU-INT-003) | "removed" (deleted from the
    # repository; restored if it comes back).
    status: Mapped[str] = mapped_column(String(16))
    # ID-HU-INT-002: who the repository shares the file with, as last read,
    # and the classification that gave.
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    access_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # ID-HU-INT-003: consecutive failed attempts, and when the next one is due
    # (app/sync/retry.py). Reset when the file syncs or someone retries it.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped["SyncSource"] = relationship(back_populates="files")


class SyncFailure(Base):
    """One failed attempt to sync a file (ID-HU-INT-003): the failure log
    administrators see in the Repositories tab. Entries of a file are marked
    resolved when a later attempt succeeds; the last one of a file whose
    retries ran out is the "needs manual review" alert."""

    __tablename__ = "sync_failures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sync_sources.id", ondelete="CASCADE"), index=True)
    remote_id: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)
    # Which attempt failed (1 = the first), out of SYNC_RETRY_MAX_ATTEMPTS.
    attempt: Mapped[int] = mapped_column(Integer)
    # "download" | "ingestion" | "permissions": the step that failed.
    stage: Mapped[str] = mapped_column(String(32))
    error: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    # Set while another attempt is planned; None once retries are exhausted.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Retries exhausted: flagged for manual review (an administrator alert).
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # When a later attempt synced the file (or it left the folder), and how.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(64), nullable=True)
