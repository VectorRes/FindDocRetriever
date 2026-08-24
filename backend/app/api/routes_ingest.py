import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.ingestion.service import ingest_excel_file
from app.schemas import DocumentOut

router = APIRouter()

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}


@router.post("/ingest", response_model=DocumentOut)
async def ingest_document(file: UploadFile, db: Session = Depends(get_db)) -> DocumentOut:
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        document = ingest_excel_file(db, filename=file.filename, file_path=tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return document
