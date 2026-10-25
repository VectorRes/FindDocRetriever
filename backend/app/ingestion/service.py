from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Cell, Document, DocumentStatus, NamedRange, PdfReference, PdfTable, Sheet
from app.embeddings.factory import get_embedding_provider
from app.ingestion.chunker import chunk_pdf, chunk_workbook
from app.ingestion.context import embedding_text
from app.ingestion.excel_parser import ExcelParsingError, parse_excel
from app.ingestion.pdf_parser import PdfParsingError, parse_pdf
from app.retrieval.vector_store import add_chunks
from app.retrieval.access import DEFAULT_TAG
from app.versioning import assign_version, recompute_group


def ingest_excel_file(
    db: Session, filename: str, file_path: Path, version_of: Document | None = None
) -> Document:
    document = Document(
        filename=filename, doc_type="excel", status=DocumentStatus.processing.value, warnings=[], confidentiality_tag=DEFAULT_TAG
    )
    db.add(document)
    db.flush()
    # ID-HU-BE-015: newest version of its group (derived from the filename).
    assign_version(db, document, version_of=version_of)

    try:
        parsed = parse_excel(file_path)
    except ExcelParsingError as exc:
        document.status = DocumentStatus.failed.value
        document.error_message = str(exc)
        recompute_group(db, document.version_group)
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
        # With the document's name, so a question naming the company finds its rows.
        embeddings = provider.embed([embedding_text(document.filename, c.text) for c in chunks])
        add_chunks(
            db,
            document_id=document.id,
            chunks=[(c.text, c.sheet_name, c.cell_range, c.page_number) for c in chunks],
            embeddings=embeddings,
        )

    document.status = DocumentStatus.ready.value
    # Decide whether this version becomes its group's default (ID-HU-BE-015).
    recompute_group(db, document.version_group)
    db.commit()
    db.refresh(document)
    return document


def ingest_pdf_file(
    db: Session, filename: str, file_path: Path, version_of: Document | None = None
) -> Document:
    document = Document(
        filename=filename, doc_type="pdf", status=DocumentStatus.processing.value, warnings=[], confidentiality_tag=DEFAULT_TAG
    )
    db.add(document)
    db.flush()
    # ID-HU-BE-015: newest version of its group (derived from the filename).
    assign_version(db, document, version_of=version_of)

    try:
        parsed = parse_pdf(file_path)
    except PdfParsingError as exc:
        document.status = DocumentStatus.failed.value
        document.error_message = str(exc)
        recompute_group(db, document.version_group)
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
        embeddings = provider.embed([embedding_text(document.filename, row[0]) for row in chunk_rows])
        add_chunks(db, document_id=document.id, chunks=chunk_rows, embeddings=embeddings)

    document.status = DocumentStatus.ready.value
    # Decide whether this version becomes its group's default (ID-HU-BE-015).
    recompute_group(db, document.version_group)
    db.commit()
    db.refresh(document)
    return document


EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
PDF_EXTENSIONS = {".pdf"}
ALLOWED_EXTENSIONS = EXCEL_EXTENSIONS | PDF_EXTENSIONS


def ingest_path(db: Session, filename: str, file_path: Path, version_of: Document | None = None) -> Document:
    """Ingest the file at `file_path` (an upload, or a file synced from a
    repository) as `filename`, and keep its original bytes: the Source
    Verification Panel's "open original file" and PDF viewer need the real
    file, not just what got extracted. Commits."""
    from app.storage import save_document_file

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {suffix}")
    ingest = ingest_pdf_file if suffix in PDF_EXTENSIONS else ingest_excel_file
    document = ingest(db, filename=filename, file_path=file_path, version_of=version_of)
    # Kept regardless of the ingestion outcome.
    document.storage_path = save_document_file(document.id, suffix, file_path)
    db.commit()
    db.refresh(document)
    return document
