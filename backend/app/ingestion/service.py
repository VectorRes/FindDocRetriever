from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Cell, Document, DocumentStatus, NamedRange, PdfReference, PdfTable, Sheet
from app.embeddings.factory import get_embedding_provider
from app.ingestion.chunker import chunk_pdf, chunk_workbook
from app.ingestion.excel_parser import ExcelParsingError, parse_excel
from app.ingestion.pdf_parser import PdfParsingError, parse_pdf
from app.retrieval.vector_store import add_chunks


def ingest_excel_file(db: Session, filename: str, file_path: Path) -> Document:
    document = Document(
        filename=filename, doc_type="excel", status=DocumentStatus.processing.value, warnings=[]
    )
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
            chunks=[(c.text, c.sheet_name, c.cell_range, c.page_number) for c in chunks],
            embeddings=embeddings,
        )

    document.status = DocumentStatus.ready.value
    db.commit()
    db.refresh(document)
    return document


def ingest_pdf_file(db: Session, filename: str, file_path: Path) -> Document:
    document = Document(
        filename=filename, doc_type="pdf", status=DocumentStatus.processing.value, warnings=[]
    )
    db.add(document)
    db.flush()

    try:
        parsed = parse_pdf(file_path)
    except PdfParsingError as exc:
        document.status = DocumentStatus.failed.value
        document.error_message = str(exc)
        db.commit()
        return document

    document.warnings = parsed.warnings

    table_ids: dict[tuple[int, int], object] = {}
    for table in parsed.tables:
        table_row = PdfTable(document_id=document.id, page_number=table.page_number,
                             table_index=table.table_index, headers=table.headers, rows=table.rows,
                             confidence=table.confidence, confidence_label=table.confidence_label,
                             raw_text=table.raw_text)
        db.add(table_row)
        db.flush()
        table_ids[(table.page_number, table.table_index)] = table_row.id
        if table.confidence_label == "low-confidence structure":
            document.warnings.append(
                f"PDF table on page {table.page_number} (table {table.table_index}) has low-confidence structure; raw page text remains available."
            )

    reference_ids: dict[tuple[str, str, int], object] = {}
    for reference in parsed.references:
        reference_row = PdfReference(document_id=document.id, page_number=reference.page_number,
                                     reference_type=reference.reference_type,
                                     reference_number=reference.reference_number,
                                     title=reference.title, text=reference.text)
        db.add(reference_row)
        db.flush()
        reference_ids[(reference.reference_type, reference.reference_number, reference.page_number)] = reference_row.id


    settings = get_settings()
    chunks = chunk_pdf(
        parsed, chunk_size=settings.pdf_chunk_size, chunk_overlap=settings.pdf_chunk_overlap
    )
    if chunks:
        provider = get_embedding_provider()
        chunk_rows = []
        for c in chunks:
            table_id = table_ids.get((c.page_number, c.table_index)) if c.content_type == "table_row" else None
            reference_id = (
                reference_ids.get((c.reference_type, c.reference_number, c.page_number))
                if c.content_type == "reference" else None
            )
            chunk_rows.append((
                c.text, c.sheet_name, c.cell_range, c.page_number, c.content_type, table_id, reference_id,
                c.extraction_method, c.confidence, c.confidence_label,
            ))
        embeddings = provider.embed([row[0] for row in chunk_rows])
        add_chunks(db, document_id=document.id, chunks=chunk_rows, embeddings=embeddings)

    document.status = DocumentStatus.ready.value
    db.commit()
    db.refresh(document)
    return document
