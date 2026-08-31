"""PDF parsing: per-page text extraction with warnings for non-extractable content.

Mirrors excel_parser.py's shape (ParsedX dataclasses + a list of warnings) so the
rest of the ingestion pipeline (chunker, service) can treat both document types
uniformly. There is no OCR here: pages with no extractable text (typically
scanned/image-only pages) are skipped and surfaced as a warning rather than
silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PdfParsingError(Exception):
    """Raised when a PDF cannot be opened/parsed at all."""


@dataclass
class ParsedPdfPage:
    number: int  # 1-indexed, for human-facing citations
    text: str


@dataclass
class ParsedPdfDocument:
    pages: list[ParsedPdfPage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_pdf(path: str | Path) -> ParsedPdfDocument:
    path = Path(path)
    try:
        reader = PdfReader(path)
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfParsingError(f"Could not open PDF '{path.name}': {exc}") from exc

    result = ParsedPdfDocument()

    if reader.is_encrypted:
        # Some encrypted PDFs use an empty user password (viewer-restrictions
        # only); try that before giving up, same trade-off openpyxl-based
        # excel_parser makes for protected sheets: warn and parse what we can.
        try:
            decrypted = reader.decrypt("") != 0
        except Exception:
            decrypted = False
        if not decrypted:
            result.warnings.append(
                f"PDF '{path.name}' is password-protected and could not be decrypted; "
                "no pages were extracted."
            )
            return result
        result.warnings.append(
            f"PDF '{path.name}' is encrypted with an empty user password; decrypted for parsing."
        )

    try:
        page_count = len(reader.pages)
    except (PdfReadError, KeyError) as exc:
        raise PdfParsingError(f"Could not read pages of PDF '{path.name}': {exc}") from exc

    empty_pages: list[int] = []
    for index in range(page_count):
        page_number = index + 1
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as exc:  # pypdf can raise a variety of parse errors per-page
            result.warnings.append(f"Page {page_number} could not be parsed: {exc}")
            continue

        text = text.strip()
        if not text:
            empty_pages.append(page_number)
            continue

        result.pages.append(ParsedPdfPage(number=page_number, text=text))

    if empty_pages:
        result.warnings.append(
            f"PDF '{path.name}' has {len(empty_pages)} page(s) with no extractable text "
            f"(likely scanned images without OCR): pages {empty_pages}."
        )

    if not result.pages:
        result.warnings.append(
            f"PDF '{path.name}' yielded no extractable text from any page."
        )

    return result
