import shutil
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.session import get_db
from app.ingestion.service import ingest_excel_file, ingest_pdf_file
from app.schemas import DocumentOut
from app.storage import save_document_file

router = APIRouter()

EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
PDF_EXTENSIONS = {".pdf"}
ALLOWED_EXTENSIONS = EXCEL_EXTENSIONS | PDF_EXTENSIONS


@router.post("/ingest", response_model=DocumentOut)
async def ingest_document(
    file: UploadFile,
    version_of: Annotated[UUID | None, Form()] = None,
    db: Session = Depends(get_db),
) -> DocumentOut:
    """Ingest a document as the newest version of its version group
    (ID-HU-BE-015). The group is derived from the filename ("Budget_v2.xlsx"
    joins "Budget_v1.xlsx"); pass `version_of` to attach it to a specific
    existing document instead. The upload starts as a draft: it only replaces
    an approved version once it is approved itself."""
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    previous_version: Document | None = None
    if version_of is not None:
        previous_version = db.get(Document, version_of)
        if previous_version is None:
            raise HTTPException(status_code=404, detail="version_of document not found")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        if suffix in PDF_EXTENSIONS:
            document = ingest_pdf_file(
                db, filename=file.filename, file_path=tmp_path, version_of=previous_version
            )
        else:
            document = ingest_excel_file(
                db, filename=file.filename, file_path=tmp_path, version_of=previous_version
            )

        # Persist the original bytes regardless of ingestion outcome — the
        # Source Verification Panel's "open original file" and PDF viewer
        # need the real file, not just what got extracted into the DB.
        document.storage_path = save_document_file(document.id, suffix, tmp_path)
        db.commit()
        db.refresh(document)
    finally:
        tmp_path.unlink(missing_ok=True)

    return document
