"""Endpoints backing ID-HU-FE-002 (Source Verification Panel): resolving a
citation's document to its live cell/formula data, current-version status,
and access decision.

Access control here is intentionally minimal: there is no auth/user system
yet, so the caller's role is taken at face value from the X-User-Role header
(default "analyst"). This is a placeholder for the full access-control story
(confidentiality tiers, real roles/permissions) — good enough to unblock the
FE-002 UI, not a real security boundary.
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Cell, Chunk, Document, DocumentStatus, Sheet
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
from app.retrieval.access import can_access, normalize_tag, parse_roles
from app.versioning import (
    APPROVER_ROLES,
    approve,
    current_version_of,
    recompute_group,
    versions_of,
)

router = APIRouter()

CONFIDENTIALITY_TAGS = {"public", "internal", "restricted"}


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
    rather than pointing at a deleted row.

    If it was its group's default version, the next best version (latest
    approved, else latest) takes over (ID-HU-BE-015)."""
    document = _get_document_or_404(db, document_id)
    version_group = document.version_group
    delete_document_file(document.storage_path)
    db.delete(document)
    recompute_group(db, version_group)
    db.commit()


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
    if can_access(document.confidentiality_tag, parse_roles(role)):
        return AccessDecision(allowed=True)
    return AccessDecision(
        allowed=False,
        reason="This content is restricted. The required permission is not assigned to this user.",
    )


@router.get("/documents/{document_id}/resolve", response_model=DocumentResolutionOut)
def resolve_document(
    document_id: str,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> DocumentResolutionOut:
    document = _get_document_or_404(db, document_id)

    # "Superseded" means a newer version replaced this one. A newer draft still
    # awaiting approval is neither current nor superseded (ID-HU-BE-015).
    is_superseded = document.superseded_by_id is not None
    current_version = current_version_of(db, document) if is_superseded else None

    return DocumentResolutionOut(
        document=document,
        is_superseded=is_superseded,
        current_version=current_version,
        access=_check_access(document, x_user_role),
    )


@router.get("/documents/{document_id}/cells/{sheet_name}/{address}", response_model=CellOut)
def get_cell(
    document_id: str,
    sheet_name: str,
    address: str,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
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
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    role: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Serves the original uploaded file — ID-HU-FE-002's "open original file"
    action, and what the PDF viewer embeds to show the real page.

    `?role=` is accepted as an alternative to X-User-Role because the browser
    loads this URL directly (iframe / link), which can't carry custom headers."""
    document = _get_document_or_404(db, document_id)

    access = _check_access(document, role or x_user_role)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    if document.storage_path is None:
        raise HTTPException(status_code=404, detail="Original file was not retained for this document")

    path = get_document_file_path(document.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Original file is missing from storage")

    return FileResponse(path, filename=document.filename)


@router.get("/documents/{document_id}/versions", response_model=DocumentListOut)
def list_versions(document_id: str, db: Session = Depends(get_db)) -> DocumentListOut:
    """Every version of this document's group, newest first — including
    superseded ones, which stay available for audit (ID-HU-BE-015)."""
    document = _get_document_or_404(db, document_id)
    return DocumentListOut(documents=versions_of(db, document))


@router.post("/documents/{document_id}/approve", response_model=DocumentOut)
def approve_document(
    document_id: str,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> DocumentOut:
    """Approve a version (ID-HU-BE-015). If it's the newest approved version
    of its group it becomes the default for future answers, and the version
    it replaces is marked superseded and linked to it."""
    if not parse_roles(x_user_role) & APPROVER_ROLES:
        raise HTTPException(
            status_code=403, detail="Approving a document version requires a reviewer role."
        )
    document = _get_document_or_404(db, document_id)
    if document.status != DocumentStatus.ready.value:
        raise HTTPException(status_code=400, detail="Only successfully processed documents can be approved.")

    approve(db, document)
    db.commit()
    db.refresh(document)
    return document


@router.post("/documents/{document_id}/supersede", response_model=DocumentOut)
def supersede_document(
    document_id: str, request: SupersedeRequest, db: Session = Depends(get_db)
) -> DocumentOut:
    """Explicitly link `document_id` as an older version of `new_document_id`
    (for files whose names don't reveal they're versions of each other).

    The document joins the newer one's version group, ordered just before it.
    Which version is the default still follows the approval rule: if
    `document_id` is approved and the newer one isn't, approve the newer one
    too (POST /documents/{id}/approve) to make it the default."""
    document = _get_document_or_404(db, document_id)
    newer = db.get(Document, request.new_document_id)
    if newer is None:
        raise HTTPException(status_code=404, detail="new_document_id not found")
    if newer.id == document.id:
        raise HTTPException(status_code=400, detail="A document cannot supersede itself")

    old_group = document.version_group
    others = [v for v in versions_of(db, newer) if v.id != document.id]  # newest first
    ordered = list(reversed(others))
    ordered.insert(ordered.index(newer), document)
    document.version_group = newer.version_group
    for number, member in enumerate(ordered, start=1):
        member.version_number = number

    recompute_group(db, newer.version_group)
    if old_group != newer.version_group:
        recompute_group(db, old_group)
    db.commit()
    db.refresh(document)
    return document


@router.patch("/documents/{document_id}/confidentiality", response_model=DocumentOut)
def set_confidentiality(
    document_id: str, request: ConfidentialityRequest, db: Session = Depends(get_db)
) -> DocumentOut:
    try:
        tag = normalize_tag(request.tag)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    document = _get_document_or_404(db, document_id)
    document.confidentiality_tag = tag
    for chunk in document.chunks:
        chunk.confidentiality_tag = tag

    db.commit()
    db.refresh(document)
    return document


@router.patch(
    "/documents/{document_id}/chunks/{chunk_id}/confidentiality",
    response_model=DocumentOut,
)
def set_chunk_confidentiality(
    document_id: str,
    chunk_id: str,
    request: ConfidentialityRequest,
    db: Session = Depends(get_db),
) -> DocumentOut:
    try:
        tag = normalize_tag(request.tag)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    document = _get_document_or_404(db, document_id)
    try:
        chunk_uuid = UUID(chunk_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid chunk id") from exc

    chunk = db.get(Chunk, chunk_uuid)
    if chunk is None or chunk.document_id != document.id:
        raise HTTPException(status_code=404, detail="Chunk not found")

    chunk.confidentiality_tag = tag
    db.commit()
    db.refresh(document)
    return document
