"""Turns parsed documents (workbooks, PDFs) into retrievable text chunks with
citation metadata."""
from __future__ import annotations

from dataclasses import dataclass

from openpyxl.utils import get_column_letter

from app.ingestion.excel_parser import ParsedSheet, ParsedWorkbook
from app.ingestion.pdf_parser import ParsedPdfDocument, ParsedPdfPage


@dataclass
class Chunk:
    sheet_name: str | None
    cell_range: str | None
    text: str
    page_number: int | None = None


def _header_row(sheet: ParsedSheet) -> tuple[int | None, dict[int, str]]:
    if not sheet.cells:
        return None, {}
    first_row = min(cell.row for cell in sheet.cells)
    headers = {
        cell.column: cell.value
        for cell in sheet.cells
        if cell.row == first_row and cell.value
    }
    return first_row, headers


def chunk_sheet(sheet: ParsedSheet) -> list[Chunk]:
    if sheet.is_empty:
        return []

    header_row_number, headers = _header_row(sheet)

    rows: dict[int, list] = {}
    for cell in sheet.cells:
        rows.setdefault(cell.row, []).append(cell)

    chunks: list[Chunk] = []
    for row_number, row_cells in sorted(rows.items()):
        if row_number == header_row_number and headers:
            continue

        row_cells = sorted(row_cells, key=lambda c: c.column)
        parts = []
        for cell in row_cells:
            col_label = headers.get(cell.column) or get_column_letter(cell.column)
            piece = f"{col_label}={cell.value if cell.value is not None else '(empty)'}"
            if cell.formula:
                piece += f" (formula: {cell.formula})"
            parts.append(piece)

        row_label = row_cells[0].value or f"row {row_number}"
        text = f"Sheet '{sheet.name}', {row_label}: " + "; ".join(parts)
        min_col = get_column_letter(row_cells[0].column)
        max_col = get_column_letter(row_cells[-1].column)
        chunks.append(
            Chunk(
                sheet_name=sheet.name,
                cell_range=f"{min_col}{row_number}:{max_col}{row_number}",
                text=text,
            )
        )

    if headers:
        summary = f"Sheet '{sheet.name}' has columns: {', '.join(headers.values())}."
        chunks.append(Chunk(sheet_name=sheet.name, cell_range=None, text=summary))

    return chunks


def chunk_workbook(workbook: ParsedWorkbook) -> list[Chunk]:
    chunks: list[Chunk] = []
    for sheet in workbook.sheets:
        chunks.extend(chunk_sheet(sheet))
    return chunks


def _split_into_windows(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Word-boundary sliding window over `text`. Never splits a word in half,
    and always makes forward progress even if a single word exceeds chunk_size."""
    words = text.split()
    if not words:
        return []

    windows: list[str] = []
    start = 0
    while start < len(words):
        window_words: list[str] = []
        length = 0
        end = start
        while end < len(words):
            projected = length + (1 if window_words else 0) + len(words[end])
            if window_words and projected > chunk_size:
                break
            window_words.append(words[end])
            length = projected
            end += 1
        windows.append(" ".join(window_words))

        if end >= len(words):
            break

        # Step forward by (window size - overlap), measured in words, so
        # consecutive chunks share roughly `chunk_overlap` characters of
        # context without re-scanning character offsets.
        overlap_words = 0
        overlap_len = 0
        i = end - 1
        while i >= start and overlap_len < chunk_overlap:
            overlap_len += len(words[i]) + 1
            overlap_words += 1
            i -= 1
        next_start = end - overlap_words
        start = next_start if next_start > start else end

    return windows


def chunk_pdf_page(page: ParsedPdfPage, chunk_size: int = 1000, chunk_overlap: int = 150) -> list[Chunk]:
    windows = _split_into_windows(page.text, chunk_size, chunk_overlap)
    return [
        Chunk(sheet_name=None, cell_range=None, text=window, page_number=page.number)
        for window in windows
    ]


def chunk_pdf(document: ParsedPdfDocument, chunk_size: int = 1000, chunk_overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in document.pages:
        chunks.extend(chunk_pdf_page(page, chunk_size=chunk_size, chunk_overlap=chunk_overlap))
    return chunks
