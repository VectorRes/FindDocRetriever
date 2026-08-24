"""Turns a parsed workbook into retrievable text chunks with citation metadata."""
from __future__ import annotations

from dataclasses import dataclass

from openpyxl.utils import get_column_letter

from app.ingestion.excel_parser import ParsedSheet, ParsedWorkbook


@dataclass
class Chunk:
    sheet_name: str | None
    cell_range: str | None
    text: str


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
