import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus
from app.db.session import get_db
from app.ingestion.service import ingest_excel_file, ingest_pdf_file
from app.schemas import DocumentOut
from app.storage import save_document_file

router = APIRouter()

EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
PDF_EXTENSIONS = {".pdf"}
ALLOWED_EXTENSIONS = EXCEL_EXTENSIONS | PDF_EXTENSIONS


@router.post("/ingest", response_model=DocumentOut)
async def ingest_document(file: UploadFile, db: Session = Depends(get_db)) -> DocumentOut:
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        if suffix in PDF_EXTENSIONS:
            document = ingest_pdf_file(db, filename=file.filename, file_path=tmp_path)
        else:
            document = ingest_excel_file(db, filename=file.filename, file_path=tmp_path)

        # Persist the original bytes regardless of ingestion outcome — the
        # Source Verification Panel's "open original file" and PDF viewer
        # need the real file, not just what got extracted into the DB.
        document.storage_path = save_document_file(document.id, suffix, tmp_path)
        db.commit()
        db.refresh(document)
    finally:
        tmp_path.unlink(missing_ok=True)

    if document.status == DocumentStatus.ready.value:
        _supersede_previous_version(db, document)

    return document


def _supersede_previous_version(db: Session, document: Document) -> None:
    """ID-HU-FE-005: uploading a file with the same name as an existing
    current document is treated as a new version of it — the previous one is
    marked superseded and linked, with no manual step required."""
    previous = db.scalar(
        select(Document).where(
            Document.filename == document.filename,
            Document.is_current.is_(True),
            Document.id != document.id,
        )
    )
    if previous is None:
        return

    previous.is_current = False
    previous.superseded_by_id = document.id
    db.commit()
