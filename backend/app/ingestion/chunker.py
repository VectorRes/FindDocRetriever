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
# Dates as statements write them: "June 27, 2026", "31 December 2025",
# "31 de diciembre de 2025".
_DATE = (
    r"(?:[A-Z][a-z]+\.?\s+\d{1,2},\s*(?:19|20)\d{2}"
    r"|\d{1,2}\s+[A-Z][a-z]+\.?\s+(?:19|20)\d{2}"
    r"|\d{1,2}\s+de\s+[A-Za-záéíóúÁÉÍÓÚ]+\s+de(?:l)?\s+(?:19|20)\d{2})"
)
_POINT_IN_TIME = re.compile(rf"\b(?:As of|As at|At|Al|A)\s+{_DATE}")
_PERIOD_GROUP = re.compile(
    r"\b((?:Three|Six|Nine|Twelve) Months Ended|Quarters? Ended|Years? Ended|Year To Date|Year-to-Date"
    r"|(?:Tres|Seis|Nueve|Doce) meses terminados?(?: el)?|Trimestres? terminados?(?: el)?"
    r"|Semestres? terminados?(?: el)?|A[ñn]os? terminados?(?: el)?|Acumulado(?: a| al)?)"
    # The group's own day ("Quarter Ended June 30"), not a full date that is a
    # column heading ("Nine Months Ended June 27, 2026 June 28, 2025"). A year
    # right after the day that starts a run of years ("June 30, 2025 2026
    # 2025 2026") is a column heading, so the day is still the group's.
    r"(\s+[A-Z][a-z]+\.?\s+\d{1,2}(?!\d)(?!,?\s*(?:19|20)\d{2}(?!\s+(?:19|20)\d{2}))"
    r"|\s+\d{1,2}\s+de\s+[a-záéíóú]+(?!\s+de\s+\d))?",
    re.IGNORECASE,
)
# Full dates, e.g. the column headings under period groups.
_FULL_DATE = re.compile(rf"\b{_DATE}\b")
# Numeric dates ("31/12/2024", "2024-12-31") and the change columns that
# often follow them in comparative statements.
_NUMERIC_DATE = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2}")
_NUMERIC_HEADING = re.compile(
    rf"(?<![\d/.-])(?:{_NUMERIC_DATE.pattern})(?![\d/.-])"
    r"|\b(?:Variaci[oó]n|Variation|Var\.)(?:\s+(?:absoluta|relativa|porcentual|absolute|relative|%))?"
    r"|%\s*(?:Change|Var(?:iaci[oó]n)?)|\bChange\b(?:\s+%)?|\bDiferencia\b",
    re.IGNORECASE,
)
HEADING_WINDOW_CHARS = 400
# The heading of a column of note references ("Nota 5", "Note 12").
_NOTE_HEADING = re.compile(r"\b(?:notas?|notes?)\b", re.IGNORECASE)
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
    """The figure in a cell, without a currency sign pdfplumber glued to it
    ("28,196 $"). A negative in the last column often loses its closing
    parenthesis to the table's clip ("(2,274"): it's restored."""
    value = " ".join(_CURRENCY_SIGNS.sub(" ", cell or "").split())
    if value.startswith("(") and ")" not in value and re.search(r"\d$", value):
        value += ")"
    return value


# A figure glued to the end of a row label, after a currency sign: "Net
# income $ 30,425", "Total activo $166,568,554 $133,375,329 -$6,081,160".
_TRAILING_FIGURE = re.compile(r"\s+(-?)(?:US\$|\$|€|£|¥)\s*(\(?-?\d[\d.,]*\)?)$")


def _attach_minus_signs(row: list[str], value_columns: list[int]) -> list[str]:
    """A minus sign the table put in its own cell ("- | 36,977") or at the
    end of the previous figure ("499 - | 209") belongs to the next figure."""
    row = list(row)
    for i, cell in enumerate(row):
        text = (cell or "").strip()
        dangling = text in {"-", "−"} and i not in value_columns
        trailing = re.fullmatch(r"(.*\d)\s+[-−]", text)
        if not (dangling or trailing):
            continue
        j = next((k for k in range(i + 1, len(row)) if (row[k] or "").strip()), None)
        if j is None or j not in value_columns or not re.fullmatch(r"\(?\d[\d.,]*\)?", row[j].strip()):
            continue
        row[j] = "-" + row[j].strip()
        row[i] = trailing.group(1) if trailing else ""
    return row


def _split_label_value(row: list[str], value_columns: list[int]) -> list[str]:
    """Move figures glued to the end of the row label back into the row's
    empty value columns, in order: PDF tables put "$" before a row's figures
    (often only on totals), and the label clip can swallow one or all of
    them. Only figures after a currency sign are taken, so a number that is
    part of the label ("Notes due 2026") stays."""
    if not row or not value_columns:
        return row
    label = (row[0] or "").strip()
    figures: list[str] = []
    while (match := _TRAILING_FIGURE.search(label)) and re.search(r"[A-Za-zÀ-ÿ]", label[:match.start()]):
        figures.insert(0, match.group(1) + match.group(2))
        label = label[:match.start()].rstrip()
    empty = [c for c in value_columns if c >= len(row) or not _is_value(row[c])]
    if not figures or len(figures) > len(empty):
        return row
    row = list(row) + [""] * max(0, max(value_columns) + 1 - len(row))
    row[0] = label
    for column, figure in zip(empty, figures):
        row[column] = figure
    return row


# Letters printed with tracking come out spaced ("T o tal revenue s").
_SPACED_LETTERS = re.compile(r"(?<![A-Za-z])[A-Za-z](?: [A-Za-z]{1,3}\b)+|\b[A-Za-z]{2,} [a-z](?![A-Za-z])")


def _respace_label(label: str, page_text: str) -> str:
    """The label as printed on the page when the table split its letters
    ("T o tal revenue s" -> "Total revenues"): its letters, ignoring spaces,
    looked up in the page's text. Unchanged when not found exactly once."""
    if not page_text or not _SPACED_LETTERS.search(label):
        return label
    letters = re.sub(r"\s+", "", label)
    if len(letters) < 4:
        return label
    positions = [i for i, ch in enumerate(page_text) if not ch.isspace()]
    compact = "".join(page_text[i] for i in positions)
    start = compact.find(letters)
    if start < 0 or compact.find(letters, start + 1) >= 0:
        return label
    original = page_text[positions[start]:positions[start + len(letters) - 1] + 1]
    return " ".join(original.split())


def _is_value(cell: str | None) -> bool:
    return bool(_NUMBER_CELL.match(_clean_value(cell)))


def _is_data_row(row: list[str]) -> bool:
    """Whether a row pdfplumber gave as the table header is really a data row
    ("Products | 47,153 | 43,620"), not headings or a title split into cells
    ("COMPARATIVO AL | 31 | DE DICIEMBRE DE 20 | … | 023")."""
    cells = [(cell or "").strip() for cell in row[1:] if (cell or "").strip()]
    values = [cell for cell in cells if _is_value(cell) and not _YEAR_CELL.fullmatch(cell)]
    if any(_is_amount(cell) for cell in values):
        return True
    words = [cell for cell in cells if cell not in values and not _CURRENCY_CELL.match(cell)]
    return len(values) >= 2 and not words


def _is_amount(cell: str | None) -> bool:
    """A figure shaped like an amount (grouped, decimal or 4+ digits), not a
    fragment of a split title ("31", "023" from "31 DE DICIEMBRE DE 2023")."""
    value = _clean_value(cell).strip("()-−% ")
    return _is_value(cell) and bool(re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+[.,]\d+|\d{4,}", value))


def _table_preamble(rows: list[list[str]], page_text: str) -> str:
    """The page text before the table's first row: title, units ("In
    millions") and column headings, which pdfplumber often drops."""
    text = " ".join(page_text.split())
    first = next((row for row in rows if row and row[0] and row[0].strip()), None)
    if first is None:
        return ""
    label = first[0].strip()
    value = next((_clean_value(cell) for cell in first[1:] if _is_value(cell)), "")
    # The label followed by its first value: a label can appear earlier on the
    # page under another section ("Products" under net sales and cost of sales).
    anchored = re.search(re.escape(label) + r"\W{0,8}" + re.escape(value), text) if value else None
    position = anchored.start() if anchored else text.find(label)
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
    # Column headings sit right above the table; text further up (a title, a
    # paragraph mentioning "as of" dates) isn't a heading.
    heading_text = preamble[-HEADING_WINDOW_CHARS:]
    dates = [" ".join(m.group(0).split()) for m in _POINT_IN_TIME.finditer(heading_text)]
    if value_columns and len(dates) == len(value_columns):
        return dict(zip(value_columns, dates))
    # "31/12/2024  31/12/2023  Variación absoluta  Variación relativa": numeric
    # dates (common outside the US) and change columns, one per value column.
    headings = [" ".join(m.group(0).split()) for m in _NUMERIC_HEADING.finditer(heading_text)]
    if value_columns and len(headings) == len(value_columns) and any(_NUMERIC_DATE.fullmatch(h) for h in headings):
        return dict(zip(value_columns, headings))

    years_runs = [re.findall(r"(?:19|20)\d{2}", m.group(0)) for m in _YEAR_RUN.finditer(heading_text)]
    years = next((run for run in reversed(years_runs) if len(run) == len(value_columns)), None)
    full_dates = [" ".join(m.group(0).split()) for m in _FULL_DATE.finditer(heading_text)]
    if years:
        labels = dict(zip(value_columns, years))
    elif value_columns and len(full_dates) >= len(value_columns):
        # "Three Months Ended  Nine Months Ended / June 27, 2026  June 28, 2025 ...";
        # the last ones, as a title can mention dates too ("Al 31 de diciembre de 2025 y 2024").
        labels = dict(zip(value_columns, full_dates[-len(value_columns):]))
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

    found_groups = [(" ".join(m.group(1).split()), " ".join((m.group(2) or "").split()))
                    for m in _PERIOD_GROUP.finditer(heading_text)]
    # The groups right above the columns: the last k that split them evenly.
    groups = next(
        (found_groups[-k:] for k in range(len(found_groups), 0, -1) if value_columns and len(value_columns) % k == 0),
        [],
    )
    if value_columns and all(labels.values()) and groups:
        size = len(value_columns) // len(groups)
        for index, column in enumerate(value_columns):
            name, day = groups[index // size]
            # A column already labelled with a full date doesn't need the group's own date.
            prefix = name if _FULL_DATE.search(labels[column]) or not day else f"{name} {day}"
            labels[column] = f"{prefix} {labels[column]}"
    return labels


def _context(preamble: str) -> str:
    if len(preamble) <= MAX_TABLE_CONTEXT_CHARS:
        return preamble
    head = MAX_TABLE_CONTEXT_CHARS // 3
    return f"{preamble[:head].rstrip()} … {preamble[-(MAX_TABLE_CONTEXT_CHARS - head):].lstrip()}"


_GROUPED_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_YEAR_CELL = re.compile(r"(?:19|20)\d{2}")


def _merge_split_numbers(row: list[str], page_text: str = "") -> list[str]:
    """pdfplumber sometimes splits a figure across two cells ("5" | "4,190"
    for 54,190). Join a 1-2 digit fragment with the grouped number right
    after it when the result is still a well-formed grouped number and, when
    the page text is known, the joined figure is printed on the page: a note
    reference next to a figure ("4" | "58,240", printed "4 58,240") must stay
    apart."""
    row = [cell or "" for cell in row]
    # Right to left, so a figure in three pieces ("1" | "8" | "7,103") chains up.
    for i in range(len(row) - 2, -1, -1):
        head = row[i].strip()
        if not re.fullmatch(r"\d{1,2}", head):
            continue
        # The rest of the figure is in the next cell, or the one after an empty cell.
        j = next((k for k in range(i + 1, min(i + 3, len(row))) if row[k].strip()), None)
        tail = row[j].strip() if j is not None else ""
        joined = head + tail
        if _GROUPED_NUMBER.fullmatch(tail) and _GROUPED_NUMBER.fullmatch(joined) and (
            not page_text or joined in page_text
        ):
            row[i], row[j] = "", joined
    return row


def _year_header_index(rows: list[list[str]]) -> int | None:
    """A row inside the table that is the real column header: only years
    ("2025 | 2026"), as pdfplumber leaves it when the table follows text."""
    for index, row in enumerate(rows):
        cells = [cell.strip() for cell in row if cell and cell.strip()]
        if len(cells) >= 2 and all(_YEAR_CELL.fullmatch(cell) for cell in cells):
            return index
    return None


def _without_note_column(
    rows: list[list[str]], header_row: list[str], value_columns: list[int], preamble: str
) -> list[int]:
    """Drop the column of note references (IFRS statements' "Nota" column:
    5, 6, 12…): small whole numbers next to the figures, under a "Nota" /
    "Note" heading. They're cross-references, not amounts."""
    if len(value_columns) < 2:
        return value_columns
    first = value_columns[0]
    cells = [_clean_value(row[first]) for row in rows if first < len(row) and _is_value(row[first])]
    headings = " ".join(cell or "" for cell in header_row) + " " + preamble[-HEADING_WINDOW_CHARS:]
    if _NOTE_HEADING.search(headings):
        if cells and all(re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", cell) for cell in cells):
            return value_columns[1:]
        return value_columns
    # Without a heading: numbered note references read like section numbers
    # ("6.1", "6.2" … "6.10"), always increasing, never like amounts.
    sections = [tuple(int(part) for part in cell.split(".")) for cell in cells
                if re.fullmatch(r"\d{1,2}\.\d{1,2}", cell)]
    if len(cells) >= 3 and len(sections) == len(cells) and sections == sorted(sections):
        return value_columns[1:]
    return value_columns


def _table_row_chunks(table: ParsedPdfTable, page_text: str = "") -> list[Chunk]:
    printed = " ".join(page_text.split())
    rows = [_merge_split_numbers(row, printed) for row in table.rows]
    header_row = table.headers
    if header_row and header_row[0] and _is_data_row(header_row):
        # pdfplumber took a data row ("Products | 47,153 | 43,620") as the header.
        rows = [_merge_split_numbers(header_row, printed), *rows]
        header_row = []
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
    value_columns = _without_note_column(rows, header_row, value_columns, preamble)
    labels = _column_labels(header_row, value_columns, preamble)
    context = _context(preamble)
    where = f"Financial table page {table.page_number}" + (f" ({context})" if context else "")

    # A heading right before the table ("... Cost of sales:") opens its first section.
    # Letters only (no parentheses or digits), so a title like "(in USD thousands)" isn't swept in.
    opening = re.search(r"([A-Za-zÁÉÍÓÚáéíóúÑñ][A-Za-zÁÉÍÓÚáéíóúÑñ ,&'/-]{1,60}):\s*$", preamble)
    section = opening.group(1).strip() if opening else None
    chunks: list[Chunk] = []
    for row in rows:
        row = _split_label_value(_attach_minus_signs(row, value_columns), value_columns)
        # Text in other columns is the rest of a wrapped label, not a value.
        words = [cell.strip() for i, cell in enumerate(row) if cell and cell.strip()
                 and i not in value_columns and not _CURRENCY_CELL.match(cell)]
        label = _respace_label(" ".join(words), printed) or "(unlabelled row)"
        values = [
            f"{labels.get(column) or f'column {position}'}={_clean_value(row[column])}"
            for position, column in enumerate(value_columns, start=1)
            if column < len(row) and _is_value(row[column])
        ]
        # Section headings ("Cost of sales:") name the rows under them: a bare
        # "Products" row is cost of sales, not net sales.
        if not values and label.endswith(":"):
            section = label.rstrip(":").strip()
        elif section and values and not label.lower().startswith("total"):
            label = f"{section}: {label}"
        elif label.lower().startswith("total"):
            section = None
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
