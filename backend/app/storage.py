"""Persists uploaded files to disk so they can be served back later (ID-HU-FE-002:
"open original file" and the PDF page viewer both need the real bytes).

Ingestion itself only ever reads from a temp path; this is a separate step
the caller (routes_ingest.py) runs after ingestion so a parsing failure still
never leaves an orphaned file with no Document row.
"""
import shutil
import uuid
from pathlib import Path

from app.config import get_settings


def save_document_file(document_id: uuid.UUID, suffix: str, tmp_path: Path) -> str:
    """Copy `tmp_path` into permanent storage and return its storage path."""
    storage_dir = Path(get_settings().document_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    dest = storage_dir / f"{document_id}{suffix}"
    shutil.copyfile(tmp_path, dest)
    return str(dest)


def get_document_file_path(storage_path: str) -> Path:
    return Path(storage_path)
