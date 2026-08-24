from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import Cell, Document, DocumentStatus, NamedRange, Sheet
from app.embeddings.factory import get_embedding_provider
from app.ingestion.chunker import chunk_workbook
from app.ingestion.excel_parser import ExcelParsingError, parse_excel
from app.retrieval.vector_store import add_chunks


def ingest_excel_file(db: Session, filename: str, file_path: Path) -> Document:
    document = Document(filename=filename, status=DocumentStatus.processing.value, warnings=[])
    db.add(document)
    db.flush()

    try:
        parsed = parse_excel(file_path)
    except ExcelParsingError as exc:
        document.status = DocumentStatus.failed.value
        document.error_message = str(exc)
        db.commit()
        return document

    for sheet in parsed.sheets:
        sheet_row = Sheet(
            document_id=document.id,
            name=sheet.name,
            is_empty=sheet.is_empty,
            is_protected=sheet.is_protected,
        )
        db.add(sheet_row)
        db.flush()

        for cell in sheet.cells:
            db.add(
                Cell(
                    sheet_id=sheet_row.id,
                    address=cell.address,
                    row=cell.row,
                    column=cell.column,
                    value=cell.value,
                    formula=cell.formula,
                    formula_references=cell.formula_references,
                    is_merged=cell.is_merged,
                    merged_range=cell.merged_range,
                )
            )

    for named_range in parsed.named_ranges:
        db.add(
            NamedRange(
                document_id=document.id,
                name=named_range.name,
                sheet_name=named_range.sheet_name,
                cell_range=named_range.cell_range,
            )
        )

    document.warnings = parsed.warnings

    chunks = chunk_workbook(parsed)
    if chunks:
        provider = get_embedding_provider()
        embeddings = provider.embed([c.text for c in chunks])
        add_chunks(
            db,
            document_id=document.id,
            chunks=[(c.text, c.sheet_name, c.cell_range) for c in chunks],
            embeddings=embeddings,
        )

    document.status = DocumentStatus.ready.value
    db.commit()
    db.refresh(document)
    return document
