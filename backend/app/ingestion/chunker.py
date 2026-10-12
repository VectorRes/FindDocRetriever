"""Turns parsed documents into retrievable chunks with citation metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass

from openpyxl.utils import get_column_letter

from app.ingestion.excel_parser import ParsedSheet, ParsedWorkbook
from app.ingestion.pdf_parser import ParsedPdfDocument, ParsedPdfPage, ParsedPdfTable, ParsedPdfReference


@dataclass
class Chunk:
    sheet_name: str | None
    cell_range: str | None
    text: str
    page_number: int | None = None
    content_type: str = "text"
    table_index: int | None = None
    reference_number: str | None = None
    reference_type: str | None = None
    # How the underlying page text was obtained ("native" or "ocr") and how
    # reliable it is. OCR-derived chunks carry a confidence lower than native
    # text so callers can flag them for verification against the scan.
    extraction_method: str = "native"
    confidence: float | None = None
    confidence_label: str | None = None


# Title/notes rows above the header that are repeated into every row chunk.
MAX_PREAMBLE_ROWS = 3
MAX_PREAMBLE_CHARS = 240


def _header_row(sheet: ParsedSheet) -> tuple[int | None, dict[int, str], list[str]]:
    """The header row, its labels, and the preamble above it.

    Financial statements usually open with a title and notes ("Andina S.A.S. —
    Estado de resultados — Año 2024", "Cifras en pesos colombianos (COP)")
    before the real header ("Concepto | 2024"). So the header is the first row
    with at least two distinct values — a title, even merged across columns,
    has one — falling back to the first row (e.g. single-column sheets). Rows
    above it are kept as preamble: they carry the entity, period and currency
    that every figure in the sheet depends on."""
    if not sheet.cells:
        return None, {}, []
    rows: dict[int, list] = {}
    for cell in sheet.cells:
        if cell.value:
            rows.setdefault(cell.row, []).append(cell)
    if not rows:
        return None, {}, []
    ordered = sorted(rows)
    header_row = next((r for r in ordered if len({c.value for c in rows[r]}) >= 2), ordered[0])
    headers = {cell.column: cell.value for cell in rows[header_row]}
    preamble = [
        " ".join(dict.fromkeys(str(c.value) for c in sorted(rows[r], key=lambda c: c.column)))
        for r in ordered if r < header_row
    ][:MAX_PREAMBLE_ROWS]
    return header_row, headers, preamble


def chunk_sheet(sheet: ParsedSheet) -> list[Chunk]:
    if sheet.is_empty:
        return []
    header_row_number, headers, preamble = _header_row(sheet)
    context = " · ".join(preamble)[:MAX_PREAMBLE_CHARS]
    sheet_label = f"Sheet '{sheet.name}' ({context})" if context else f"Sheet '{sheet.name}'"
    rows: dict[int, list] = {}
    for cell in sheet.cells:
        rows.setdefault(cell.row, []).append(cell)
    chunks: list[Chunk] = []
    for row_number, row_cells in sorted(rows.items()):
        if header_row_number is not None and row_number < header_row_number and preamble:
            continue  # folded into every chunk as context
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
        text = f"{sheet_label}, {row_label}: " + "; ".join(parts)
        min_col = get_column_letter(row_cells[0].column)
        max_col = get_column_letter(row_cells[-1].column)
        chunks.append(Chunk(sheet_name=sheet.name, cell_range=f"{min_col}{row_number}:{max_col}{row_number}", text=text))
    if headers:
        chunks.append(Chunk(sheet_name=sheet.name, cell_range=None, text=f"{sheet_label} has columns: {', '.join(map(str, headers.values()))}."))
    return chunks


def chunk_workbook(workbook: ParsedWorkbook) -> list[Chunk]:
    return [chunk for sheet in workbook.sheets for chunk in chunk_sheet(sheet)]


def _split_into_windows(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
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
        overlap_words = 0
        overlap_len = 0
        i = end - 1
        while i >= start and overlap_len < chunk_overlap:
            overlap_len += len(words[i]) + 1
            overlap_words += 1
            i -= 1
        start = max(end - overlap_words, start + 1)
    return windows


def chunk_pdf_page(page: ParsedPdfPage, chunk_size: int = 1000, chunk_overlap: int = 150) -> list[Chunk]:
    return [
        Chunk(
            None,
            None,
            window,
            page.number,
            extraction_method=page.extraction_method,
            confidence=page.confidence,
            confidence_label=page.confidence_label,
        )
        for window in _split_into_windows(page.text, chunk_size, chunk_overlap)
    ]


# Cells pdfplumber splits off on their own: a currency sign printed in its own
# column ("$ 96,428") is not a value and must not take the value's header.
_CURRENCY_CELL = re.compile(r"^\s*(\$|US\$|€|£|¥|COP|USD|EUR)\s*$")
# Column headings found in the text above a table, in reading order.
_POINT_IN_TIME = re.compile(r"\b(?:As of|As at|At)\s+[A-Z][a-z]+\.?\s+\d{1,2},\s*\d{4}")
_PERIOD_GROUP = re.compile(
    r"\b(?:(?:Three|Six|Nine|Twelve) Months Ended|Quarters? Ended|Years? Ended|Year To Date|Year-to-Date)"
    r"\s+[A-Z][a-z]+\.?\s+\d{1,2}",
    re.IGNORECASE,
)
MAX_TABLE_CONTEXT_CHARS = 320
# Something a period column header contains: a year, a quarter or a month.
_PERIOD_TOKEN = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?!\d)|\bQ[1-4]\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|abr|ago|dic)[a-z]*\b",
    re.IGNORECASE,
)


_CURRENCY_SIGNS = re.compile(r"US\$|\$|€|£|¥")
_NUMBER_CELL = re.compile(r"^\(?[-−]?\s*\d[\d.,]*\s*\)?\s*%?$|^[—–-]$")
# A run of years above the columns ("2025 2026 2025 2026").
_YEAR_RUN = re.compile(r"(?:(?<!\d)(?:19|20)\d{2}(?!\d)[\s,]*){2,}")


def _clean_value(cell: str | None) -> str:
    """The figure in a cell, without a currency sign pdfplumber glued to it ("28,196 $")."""
    return " ".join(_CURRENCY_SIGNS.sub(" ", cell or "").split())


def _is_value(cell: str | None) -> bool:
    return bool(_NUMBER_CELL.match(_clean_value(cell)))


def _table_preamble(rows: list[list[str]], page_text: str) -> str:
    """The page text before the table's first row: title, units ("In
    millions") and column headings, which pdfplumber often drops."""
    text = " ".join(page_text.split())
    first_label = next((row[0].strip() for row in rows if row and row[0] and row[0].strip()), "")
    position = text.find(first_label) if first_label else -1
    return text[:position].strip() if position > 0 else ""


def _column_labels(header_row: list[str], value_columns: list[int], preamble: str) -> dict[int, str]:
    """A header for each value column.

    - Point-in-time headings above the table ("As of December 31, 2025 /
      As of June 30, 2026"), when there's exactly one per value column — a
      balance sheet, whose header row pdfplumber often mangles.
    - Otherwise the table's own header row: the nearest header at or left of
      each value column (a currency-sign column can shift it), prefixed with
      the period groups above the table when they divide the columns evenly
      ("Quarter Ended June 30 2026" vs "Year To Date June 30 2026").
    The header row is used only if it gives every value column a header: a
    partial one is usually a fragment of a sentence ("ne 30, 2026.") and
    would put a wrong period on a value, which is worse than none. Columns
    left without a header are labelled by position by the caller."""
    dates = [" ".join(m.group(0).split()) for m in _POINT_IN_TIME.finditer(preamble)]
    if value_columns and len(dates) == len(value_columns):
        return dict(zip(value_columns, dates))

    years_runs = [re.findall(r"(?:19|20)\d{2}", m.group(0)) for m in _YEAR_RUN.finditer(preamble)]
    years = next((run for run in reversed(years_runs) if len(run) == len(value_columns)), None)
    if years:
        labels = dict(zip(value_columns, years))
    else:
        headers = [h.strip() if h else "" for h in header_row]
        labels = {}
        previous = 0
        for column in value_columns:
            labels[column] = next(
                (headers[i] for i in range(min(column, len(headers) - 1), previous, -1) if headers[i]), ""
            )
            previous = column
        if not all(labels.values()):
            return {}
        # Some headers name a period and others don't ("2025", "orte", "ct
        # Reven"): the row mixes headers of different tables or fragments.
        dated = [bool(_PERIOD_TOKEN.search(label)) for label in labels.values()]
        if any(dated) and not all(dated):
            return {}

    groups = [" ".join(m.group(0).split()) for m in _PERIOD_GROUP.finditer(preamble)]
    if value_columns and all(labels.values()) and groups and len(value_columns) % len(groups) == 0:
        size = len(value_columns) // len(groups)
        for index, column in enumerate(value_columns):
            labels[column] = f"{groups[index // size]} {labels[column]}"
    return labels


def _context(preamble: str) -> str:
    if len(preamble) <= MAX_TABLE_CONTEXT_CHARS:
        return preamble
    head = MAX_TABLE_CONTEXT_CHARS // 3
    return f"{preamble[:head].rstrip()} … {preamble[-(MAX_TABLE_CONTEXT_CHARS - head):].lstrip()}"


_GROUPED_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_YEAR_CELL = re.compile(r"(?:19|20)\d{2}")


def _merge_split_numbers(row: list[str]) -> list[str]:
    """pdfplumber sometimes splits a figure across two cells ("5" | "4,190"
    for 54,190). Join a 1-2 digit fragment with the grouped number right
    after it when the result is still a well-formed grouped number."""
    row = [cell or "" for cell in row]
    # Right to left, so a figure in three pieces ("1" | "8" | "7,103") chains up.
    for i in range(len(row) - 2, -1, -1):
        head = row[i].strip()
        if not re.fullmatch(r"\d{1,2}", head):
            continue
        # The rest of the figure is in the next cell, or the one after an empty cell.
        j = next((k for k in range(i + 1, min(i + 3, len(row))) if row[k].strip()), None)
        tail = row[j].strip() if j is not None else ""
        if _GROUPED_NUMBER.fullmatch(tail) and _GROUPED_NUMBER.fullmatch(head + tail):
            row[i], row[j] = "", head + tail
    return row


def _year_header_index(rows: list[list[str]]) -> int | None:
    """A row inside the table that is the real column header: only years
    ("2025 | 2026"), as pdfplumber leaves it when the table follows text."""
    for index, row in enumerate(rows):
        cells = [cell.strip() for cell in row if cell and cell.strip()]
        if len(cells) >= 2 and all(_YEAR_CELL.fullmatch(cell) for cell in cells):
            return index
    return None


def _table_row_chunks(table: ParsedPdfTable, page_text: str = "") -> list[Chunk]:
    rows = [_merge_split_numbers(row) for row in table.rows]
    header_row = table.headers
    year_header = _year_header_index(rows)
    if year_header is not None:
        # Rows above it are page text pdfplumber swept into the table (already
        # in the page's text chunks); the year row is the header.
        header_row, rows = rows[year_header], rows[year_header + 1:]

    width = max([len(header_row), *(len(row) for row in rows)] or [0])
    counts = {
        column: sum(1 for row in rows if column < len(row) and _is_value(row[column]))
        for column in range(1, width)
    }
    # The figure columns, not a number that happens to sit in a wrapped label
    # (a par value "$0.001" in one row): numbers in a fair share of the rows.
    busiest = max(counts.values(), default=0)
    value_columns = [c for c, n in counts.items() if n and n >= max(1, 0.3 * busiest)]
    preamble = _table_preamble(rows, page_text)
    labels = _column_labels(header_row, value_columns, preamble)
    context = _context(preamble)
    where = f"Financial table page {table.page_number}" + (f" ({context})" if context else "")

    chunks: list[Chunk] = []
    for row in rows:
        # Text in other columns is the rest of a wrapped label, not a value.
        words = [cell.strip() for i, cell in enumerate(row) if cell and cell.strip()
                 and i not in value_columns and not _CURRENCY_CELL.match(cell)]
        label = " ".join(words) or "(unlabelled row)"
        values = [
            f"{labels.get(column) or f'column {position}'}={_clean_value(row[column])}"
            for position, column in enumerate(value_columns, start=1)
            if column < len(row) and _is_value(row[column])
        ]
        text = f"{where}, row '{label}': " + "; ".join(values)
        chunks.append(
            Chunk(None, None, text, table.page_number, "table_row", table.table_index)
        )
    return chunks


def _reference_chunks(reference: ParsedPdfReference) -> list[Chunk]:
    text = f"{reference.reference_type.title()} {reference.reference_number}"
    if reference.title:
        text += f": {reference.title}"
    text += f" (page {reference.page_number})"
    return [Chunk(None, None, text + f". Source: {reference.text}", reference.page_number, "reference", None, reference.reference_number, reference.reference_type)]


def chunk_pdf(document: ParsedPdfDocument, chunk_size: int = 1000, chunk_overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in document.pages:
        chunks.extend(chunk_pdf_page(page, chunk_size=chunk_size, chunk_overlap=chunk_overlap))
    page_texts = {page.number: page.text for page in document.pages}
    for table in document.tables:
        chunks.extend(_table_row_chunks(table, page_texts.get(table.page_number, "")))
    for reference in document.references:
        chunks.extend(_reference_chunks(reference))
    return chunks
