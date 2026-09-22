"""Endpoints backing ID-HU-FE-002 (Source Verification Panel): resolving a
citation's document to its live cell/formula data, current-version status,
and access decision.

Access control here is intentionally minimal: there is no auth/user system
yet, so the caller's role is taken at face value from the X-User-Role header
(default "analyst"). This is a placeholder for the full access-control story
(confidentiality tiers, real roles/permissions) — good enough to unblock the
FE-002 UI, not a real security boundary.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Cell, Document, Sheet
from app.db.session import get_db
from app.schemas import (
    AccessDecision,
    CellOut,
    ConfidentialityRequest,
    DocumentListOut,
    DocumentOut,
    DocumentResolutionOut,
    SupersedeRequest,
)
from app.storage import delete_document_file, get_document_file_path

router = APIRouter()


@router.get("/documents", response_model=DocumentListOut)
def list_documents(db: Session = Depends(get_db)) -> DocumentListOut:
    """Backs ID-HU-FE-005's upload/status list: every document with its
    processing status, version links, and (for Excel) detected sheets."""
    documents = db.scalars(select(Document).order_by(Document.uploaded_at.desc())).all()
    return DocumentListOut(documents=list(documents))


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: str, db: Session = Depends(get_db)) -> None:
    """Removes a document (ID-HU-FE-005: undoing an accidental upload).
    Cascades to its sheets/cells/chunks/PDF tables; any document that names
    this one as its superseded_by_id falls back to null (ON DELETE SET NULL)
    rather than pointing at a deleted row."""
    document = _get_document_or_404(db, document_id)
    delete_document_file(document.storage_path)
    db.delete(document)
    db.commit()

ADMIN_ROLE = "admin"
CONFIDENTIALITY_TAGS = {"public", "restricted"}


def _get_document_or_404(db: Session, document_id: str) -> Document:
    try:
        document_uuid = UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document id") from exc

    document = db.get(Document, document_uuid)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _check_access(document: Document, role: str) -> AccessDecision:
    if document.confidentiality_tag == "public" or role == ADMIN_ROLE:
        return AccessDecision(allowed=True)
    return AccessDecision(
        allowed=False,
        reason="This document is restricted. Elevated permissions are required to view it.",
    )


@router.get("/documents/{document_id}/resolve", response_model=DocumentResolutionOut)
def resolve_document(
    document_id: str,
    x_user_role: str = Header(default="analyst", alias="X-User-Role"),
    db: Session = Depends(get_db),
) -> DocumentResolutionOut:
    document = _get_document_or_404(db, document_id)

    current_version: Document | None = None
    if not document.is_current and document.superseded_by_id is not None:
        current_version = db.get(Document, document.superseded_by_id)

    return DocumentResolutionOut(
        document=document,
        is_superseded=not document.is_current,
        current_version=current_version,
        access=_check_access(document, x_user_role),
    )


@router.get("/documents/{document_id}/cells/{sheet_name}/{address}", response_model=CellOut)
def get_cell(
    document_id: str,
    sheet_name: str,
    address: str,
    x_user_role: str = Header(default="analyst", alias="X-User-Role"),
    db: Session = Depends(get_db),
) -> CellOut:
    document = _get_document_or_404(db, document_id)

    access = _check_access(document, x_user_role)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    sheet = db.scalar(
        select(Sheet).where(Sheet.document_id == document.id, Sheet.name == sheet_name)
    )
    if sheet is None:
        raise HTTPException(status_code=404, detail="Sheet not found")

    cell = db.scalar(
        select(Cell).where(Cell.sheet_id == sheet.id, Cell.address == address.upper())
    )
    if cell is None:
        raise HTTPException(status_code=404, detail="Cell not found")

    return CellOut(
        sheet_name=sheet.name,
        address=cell.address,
        value=cell.value,
        formula=cell.formula,
        formula_references=cell.formula_references,
    )


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: str,
    x_user_role: str = Header(default="analyst", alias="X-User-Role"),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Serves the original uploaded file — ID-HU-FE-002's "open original file"
    action, and what the PDF viewer embeds to show the real page."""
    document = _get_document_or_404(db, document_id)

    access = _check_access(document, x_user_role)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    if document.storage_path is None:
        raise HTTPException(status_code=404, detail="Original file was not retained for this document")

    path = get_document_file_path(document.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Original file is missing from storage")

    return FileResponse(path, filename=document.filename)


@router.post("/documents/{document_id}/supersede", response_model=DocumentOut)
def supersede_document(
    document_id: str, request: SupersedeRequest, db: Session = Depends(get_db)
) -> DocumentOut:
    """Mark `document_id` as superseded by `new_document_id` (ID-HU-FE-002's
    "newer approved version exists" warning). Linking is explicit — there is
    no automatic "same filename = new version" detection during ingestion."""
    document = _get_document_or_404(db, document_id)
    newer = db.get(Document, request.new_document_id)
    if newer is None:
        raise HTTPException(status_code=404, detail="new_document_id not found")

    document.is_current = False
    document.superseded_by_id = newer.id
    db.commit()
    db.refresh(document)
    return document


@router.patch("/documents/{document_id}/confidentiality", response_model=DocumentOut)
def set_confidentiality(
    document_id: str, request: ConfidentialityRequest, db: Session = Depends(get_db)
) -> DocumentOut:
    if request.tag not in CONFIDENTIALITY_TAGS:
        raise HTTPException(
            status_code=400, detail=f"tag must be one of {sorted(CONFIDENTIALITY_TAGS)}"
        )

    document = _get_document_or_404(db, document_id)
    document.confidentiality_tag = request.tag
    db.commit()
    db.refresh(document)
    return document
