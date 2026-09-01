"""Hybrid PDF parsing with text, OCR, table and note/section extraction."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.config import get_settings


class PdfParsingError(Exception):
    """Raised when a PDF cannot be opened/parsed at all."""


NATIVE_TEXT_LABEL = "native text"
OCR_LOW_CONFIDENCE_LABEL = "low confidence — verify against scan"
OCR_HIGH_CONFIDENCE_LABEL = "ocr text"


@dataclass
class ParsedPdfPage:
    number: int
    text: str
    # "native" for text extracted directly from the PDF's text layer, "ocr" for
    # text recovered by running OCR on a rendered image of the page.
    extraction_method: str = "native"
    # Confidence in [0, 1]. Native extraction is treated as fully reliable (1.0);
    # OCR confidence is the average per-word Tesseract confidence for the page.
    confidence: float = 1.0
    confidence_label: str = NATIVE_TEXT_LABEL


@dataclass
class ParsedPdfTable:
    page_number: int
    table_index: int
    headers: list[str]
    rows: list[list[str]]
    confidence: float
    confidence_label: str
    raw_text: str


@dataclass
class ParsedPdfReference:
    page_number: int
    reference_type: str  # note | section
    reference_number: str
    title: str | None
    text: str


@dataclass
class ParsedPdfDocument:
    pages: list[ParsedPdfPage] = field(default_factory=list)
    tables: list[ParsedPdfTable] = field(default_factory=list)
    references: list[ParsedPdfReference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalise_text(text: str) -> str:
    return text.strip()


def _has_usable_native_text(text: str, min_chars: int) -> bool:
    return len("".join(text.split())) >= min_chars


def _average_word_confidence(ocr_data: dict[str, list]) -> float:
    """Average Tesseract's per-word confidence (0-100, -1 for non-text elements) to [0, 1]."""
    scores = [int(c) for c in ocr_data.get("conf", []) if str(c) not in ("-1", "") and int(c) >= 0]
    if not scores:
        return 0.0
    return round((sum(scores) / len(scores)) / 100, 3)


def _ocr_pdf_page(path: Path, page_index: int, *, dpi: int, language: str) -> tuple[str, float]:
    try:
        with fitz.open(path) as document:
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    except Exception as exc:
        raise PdfParsingError(f"Could not render page {page_index + 1} for OCR: {exc}") from exc

    try:
        text = pytesseract.image_to_string(image, lang=language).strip()
        ocr_data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as exc:
        raise PdfParsingError("Tesseract OCR is not installed or is not available on PATH.") from exc
    except pytesseract.TesseractError as exc:
        raise PdfParsingError(f"Tesseract OCR failed on page {page_index + 1}: {exc}") from exc

    return text, _average_word_confidence(ocr_data)


_VALUE_TOKEN_RE = re.compile(r"^(?:\$|€|£|[-–—]|\(?[-+]?\d[\d,]*(?:\.\d+)?%?\)?)$")
_SPLIT_DIGIT_RE = re.compile(r"^\d+$")


def _recover_row_label(
    page: "fitz.Page", raw_row: list[Any], row_cells: list[tuple[float, float, float, float]]
) -> tuple[str, int | None]:
    """Recover a row's label when PyMuPDF's text-strategy table grid truncates it.

    ``find_tables(strategy="text")`` derives one shared column grid for the whole
    table (typically from the numeric columns, which are well aligned) and then
    clips every row to that same grid. Financial statements routinely indent
    line items ("Products") further left than section headers ("Total"), and a
    grid built from the majority of rows can put column 0's left edge to the
    *right* of an indented label, silently dropping its leading characters.
    Longer labels ("Total net sales (1)") can likewise overflow to the right
    into the next, otherwise-empty column and get split there.

    We recover the true label by re-reading the page directly: find the first
    cell in the row that looks like a value (a currency symbol, a number, or a
    dash) and re-extract everything to its left, using the page's own left
    margin so indentation can never truncate the label.
    """
    value_index: int | None = None
    for i in range(1, len(raw_row)):
        text = str(raw_row[i] or "").strip()
        if text and _VALUE_TOKEN_RE.match(text):
            value_index = i
            break

    if not row_cells:
        return str(raw_row[0] or "") if raw_row else "", value_index

    y0, y1 = row_cells[0][1], row_cells[0][3]
    right_edge = row_cells[value_index][0] if value_index is not None else row_cells[0][2]
    clip = fitz.Rect(page.rect.x0, y0, right_edge, y1)
    recovered = page.get_text("text", clip=clip).strip()
    return recovered or str(raw_row[0] or ""), value_index


def _merge_split_digit_cells(row: list[str]) -> list[str]:
    """Merge a year that PyMuPDF split across two adjacent cells (e.g. "20"/"26").

    This happens on the same isolated header rows described in
    :func:`_recover_row_label`: a short, isolated token like "2026" can fall
    across a column boundary that was tuned for a different row, splitting it
    into two purely-numeric fragments that together total 4 digits.
    """
    merged = list(row)
    i = 0
    while i < len(merged) - 1:
        left, right = merged[i].strip(), merged[i + 1].strip()
        if left and right and _SPLIT_DIGIT_RE.match(left) and _SPLIT_DIGIT_RE.match(right) and len(left) + len(right) == 4:
            merged[i] = left + right
            merged[i + 1] = ""
            i += 2
        else:
            i += 1
    return merged


def _clean_table_rows(raw_rows: list[list[Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_row in raw_rows:
        row = [re.sub(r"\s+", " ", str(value or "")).strip() for value in raw_row]
        row = _merge_split_digit_cells(row)
        if any(row):
            rows.append(row)
    return rows


def _drop_uninformative_columns(headers: list[str], rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Drop columns that are empty in the header and in every data row.

    Financial statements often place a "$" in its own column, separate from
    the amount. Those spacer columns are legitimate, but a column that is
    *always* empty (no header and no value in any row) is pure PyMuPDF grid
    noise left over from the title/caption rows and just dilutes the table.
    """
    if not rows:
        return headers, rows
    keep = [i for i in range(len(headers)) if headers[i] or any(row[i] for row in rows)]
    if len(keep) == len(headers):
        return headers, rows
    new_headers = [headers[i] for i in keep]
    new_rows = [[row[i] for i in keep] for row in rows]
    return new_headers, new_rows


def _table_confidence(rows: list[list[str]], headers: list[str]) -> tuple[float, str]:
    if not rows or len(headers) < 2:
        return 0.0, "low-confidence structure"

    width = len(headers)
    consistent = sum(1 for row in rows if len(row) == width) / len(rows)
    nonempty = sum(sum(bool(v) for v in row) / width for row in rows) / len(rows)
    numeric_cells = sum(
        bool(re.search(r"(?:\(?\$?\d[\d,]*(?:\.\d+)?%?\)?|-)", value))
        for row in rows
        for value in row[1:]
    )
    numeric_total = max(1, sum(max(0, len(row) - 1) for row in rows))
    numeric_ratio = numeric_cells / numeric_total
    score = 0.45 * consistent + 0.25 * nonempty + 0.30 * numeric_ratio
    return round(score, 3), "high-confidence structure" if score >= 0.75 else "low-confidence structure"


def _extract_tables(path: Path, page_number: int) -> list[ParsedPdfTable]:
    """Use PyMuPDF's text-based table finder, which works for borderless financial tables."""
    tables: list[ParsedPdfTable] = []
    with fitz.open(path) as document:
        page = document.load_page(page_number - 1)
        try:
            found = page.find_tables(strategy="text")
        except Exception:
            return tables

        for index, table in enumerate(found.tables, start=1):
            raw_rows = table.extract()

            # Recover row labels that the shared column grid truncated (see
            # _recover_row_label) before doing any other cleanup, since later
            # steps operate on plain text and lose the page geometry needed to
            # fix this.
            repaired_rows: list[list[Any]] = []
            for row_index, raw_row in enumerate(raw_rows):
                row_cells = table.rows[row_index].cells if row_index < len(table.rows) else []
                label, value_index = _recover_row_label(page, raw_row, row_cells)
                repaired = list(raw_row)
                if repaired:
                    repaired[0] = label
                    # Clear the columns we just folded back into the label so
                    # their leftover fragments aren't mistaken for values.
                    if value_index is not None:
                        for col in range(1, value_index):
                            repaired[col] = ""
                repaired_rows.append(repaired)

            rows = _clean_table_rows(repaired_rows)
            if not rows:
                continue

            # PyMuPDF can emit spacer rows. Drop them and trim trailing empty cells.
            width = max(len(row) for row in rows)
            normalised = [row + [""] * (width - len(row)) for row in rows]
            while normalised and not any(normalised[0]):
                normalised.pop(0)
            if not normalised:
                continue

            header_index = 0
            # Financial tables commonly have several title/caption/units rows above
            # the actual period header (e.g. "Apple Inc." / "(Unaudited)" /
            # "(In millions...)" / "Three Months Ended" / "June 27," / "2026"), so we
            # scan a generous window rather than just the first few rows. We also
            # check the row's cells concatenated together, since an isolated year
            # can land in a row by itself and get split across two cells (e.g.
            # "20"/"26") by the same grid-mismatch issue described above.
            period_re = re.compile(r"(?:19|20)\d{2}|(?:fy|q[1-4]|year|period|month)", re.I)
            numeric_re = re.compile(r"(?:\(?[-+]?(?:\$|€|£)?\d[\d,]*(?:\.\d+)?%?\)?|-)$")
            scan_limit = min(len(normalised), 15)
            for candidate_index in range(scan_limit):
                candidate = normalised[candidate_index]
                populated = [value for value in candidate if value]
                joined = "".join(candidate)
                is_period_row = (
                    len(populated) >= 2 and any(period_re.search(value) for value in populated)
                ) or bool(period_re.search(joined))
                if is_period_row:
                    header_index = candidate_index
                # Once we reach a row that already looks like real tabular data
                # (several value-like cells) at or after our best header guess so
                # far, stop scanning; the header can't be any further down.
                value_cells = sum(1 for value in candidate[1:] if numeric_re.match(value.replace(" ", "")))
                if header_index > 0 and candidate_index >= header_index and value_cells >= 2:
                    break
            headers = normalised[header_index]
            data_rows = normalised[header_index + 1:]
            # Financial tables should associate values with period columns. Rows that
            # only contain prose (often the next Note/Section heading) are not table rows.
            financial_rows = [
                row for row in data_rows
                if any(numeric_re.match(value.replace(" ", "")) for value in row[1:])
            ]
            if financial_rows:
                data_rows = financial_rows
            else:
                continue
            headers, data_rows = _drop_uninformative_columns(headers, data_rows)
            confidence, label = _table_confidence(data_rows, headers)
            raw_text = "\n".join(" | ".join(row) for row in normalised)
            tables.append(
                ParsedPdfTable(
                    page_number=page_number,
                    table_index=index,
                    headers=headers,
                    rows=data_rows,
                    confidence=confidence,
                    confidence_label=label,
                    raw_text=raw_text,
                )
            )
    return tables


_NOTE_RE = re.compile(r"^\s*(?:note|nota)\s+(\d+[A-Za-z]?)\s*[:.\-]?\s*(.*)$", re.I)
_SECTION_RE = re.compile(
    r"^\s*(?:section|sección)\s+(\d+(?:\.\d+)*)\s*[:.\-]?\s*(.*)$", re.I
)
_BARE_SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\s+(.+)$")
_BARE_NOTE_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+(.+)$")


def _looks_like_financial_table(text: str) -> bool:
    numeric_re = re.compile(r"(?:\(?[-+]?(?:\$|€|£)?\d[\d,]*(?:\.\d+)?%?\)?)")
    candidates = 0
    for line in text.splitlines():
        if len(numeric_re.findall(line)) >= 2:
            candidates += 1
    return candidates >= 2


def _extract_references(page: ParsedPdfPage) -> list[ParsedPdfReference]:
    lines = [line.strip() for line in page.text.splitlines() if line.strip()]
    references: list[ParsedPdfReference] = []
    for line in lines:
        match = _NOTE_RE.match(line)
        if match:
            number, title = match.groups()
            references.append(
                ParsedPdfReference(page.number, "note", number, title or None, line)
            )
            continue

        match = _SECTION_RE.match(line)
        if match:
            number, title = match.groups()
            references.append(
                ParsedPdfReference(page.number, "section", number, title or None, line)
            )
            continue

        match = _BARE_SECTION_RE.match(line)
        if match and len(match.group(1).split(".")) >= 2:
            number, title = match.groups()
            references.append(ParsedPdfReference(page.number, "section", number, title, line))
            continue

        match = _BARE_NOTE_RE.match(line)
        if match:
            number, title = match.groups()
            references.append(ParsedPdfReference(page.number, "note", number, title, line))

    # A financial statement can introduce a note with an explicit label
    # (e.g. ``Note 1: General information``) and later repeat the same
    # number as a more specific heading (e.g. ``1. Accounting policies``).
    # Keep one structured reference per (type, number, page), but prefer the
    # most specific/latest titled occurrence instead of always preferring the
    # explicit ``Note`` prefix.
    #
    # This is important because the latter heading is often the title users
    # actually want to query. The database also enforces uniqueness for these
    # fields, so returning both occurrences would create a duplicate reference.
    unique: dict[tuple[str, str], ParsedPdfReference] = {}
    for reference in references:
        key = (reference.reference_type, reference.reference_number)
        existing = unique.get(key)

        if existing is None:
            unique[key] = reference
            continue

        # Prefer a later occurrence when it has a non-empty title. This
        # handles "Note 1: General information" followed by
        # "1. Accounting policies".
        if reference.title and reference.title != existing.title:
            unique[key] = reference

    return list(unique.values())


def parse_pdf(path: str | Path) -> ParsedPdfDocument:
    path = Path(path)
    try:
        reader = PdfReader(path)
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfParsingError(f"Could not open PDF '{path.name}': {exc}") from exc

    result = ParsedPdfDocument()
    settings = get_settings()

    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("") != 0
        except Exception:
            decrypted = False
        if not decrypted:
            result.warnings.append(
                f"PDF '{path.name}' is password-protected and could not be decrypted; no pages were extracted."
            )
            return result
        result.warnings.append(
            f"PDF '{path.name}' is encrypted with an empty user password; decrypted for parsing."
        )

    try:
        page_count = len(reader.pages)
    except (PdfReadError, KeyError) as exc:
        raise PdfParsingError(f"Could not read pages of PDF '{path.name}': {exc}") from exc

    ocr_pages: list[int] = []
    failed_pages: list[int] = []

    # A single PyMuPDF document is reused for table detection below.
    for index in range(page_count):
        page_number = index + 1
        native_text = ""
        try:
            native_text = _normalise_text(reader.pages[index].extract_text() or "")
        except Exception as exc:
            result.warnings.append(
                f"Page {page_number} native text extraction failed ({exc}); trying OCR."
            )

        if _has_usable_native_text(native_text, settings.pdf_ocr_min_native_chars):
            parsed_page = ParsedPdfPage(number=page_number, text=native_text)
            result.pages.append(parsed_page)
        else:
            if not settings.pdf_ocr_enabled:
                failed_pages.append(page_number)
                continue
            try:
                raw_ocr_text, ocr_confidence = _ocr_pdf_page(
                    path, index, dpi=settings.pdf_ocr_dpi, language=settings.pdf_ocr_language
                )
                ocr_text = _normalise_text(raw_ocr_text)
            except PdfParsingError as exc:
                result.warnings.append(f"Page {page_number} OCR failed: {exc}")
                failed_pages.append(page_number)
                continue
            if ocr_text:
                is_low_confidence = ocr_confidence < settings.pdf_ocr_low_confidence_threshold
                confidence_label = OCR_LOW_CONFIDENCE_LABEL if is_low_confidence else OCR_HIGH_CONFIDENCE_LABEL
                parsed_page = ParsedPdfPage(
                    number=page_number,
                    text=ocr_text,
                    extraction_method="ocr",
                    confidence=ocr_confidence,
                    confidence_label=confidence_label,
                )
                result.pages.append(parsed_page)
                ocr_pages.append(page_number)
                if is_low_confidence:
                    result.warnings.append(
                        f"Page {page_number}: OCR confidence is low ({ocr_confidence:.0%}) — "
                        f"{OCR_LOW_CONFIDENCE_LABEL}."
                    )
            else:
                failed_pages.append(page_number)
                continue

        result.references.extend(_extract_references(parsed_page))
        page_tables = _extract_tables(path, page_number)
        result.tables.extend(page_tables)
        if not page_tables and _looks_like_financial_table(parsed_page.text):
            result.warnings.append(
                f"Page {page_number} appears to contain tabular financial data, but no reliable structure was detected; flagged as low-confidence structure and raw page text is retained."
            )

    if ocr_pages:
        result.warnings.append(f"OCR was used for {len(ocr_pages)} scanned/image-only page(s): {ocr_pages}.")
    if failed_pages:
        result.warnings.append(
            f"PDF '{path.name}' has {len(failed_pages)} page(s) from which no text could be recovered: pages {failed_pages}."
        )
    if not result.pages:
        result.warnings.append(f"PDF '{path.name}' yielded no extractable or OCR text from any page.")

    return result
