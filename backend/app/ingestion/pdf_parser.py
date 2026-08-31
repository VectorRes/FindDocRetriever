"""Hybrid PDF parsing with native text extraction and OCR fallback.

Each page is handled independently:
- If pypdf can extract text, that text is used directly (fast path).
- If the page has no usable text, the page is rendered to an image and OCR is
  performed with Tesseract.

This page-level strategy also supports mixed PDFs containing both digital and
scanned pages without OCRing pages that already contain machine-readable text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.config import get_settings


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


def _normalise_text(text: str) -> str:
    """Trim extracted text while preserving useful line structure."""
    return text.strip()


def _has_usable_native_text(text: str, min_chars: int) -> bool:
    """Return True when native extraction produced enough non-whitespace text.

    The default threshold is intentionally very small (1 character), meaning OCR
    is normally only used for genuinely image-only pages. It can be raised with
    PDF_OCR_MIN_NATIVE_CHARS for PDFs whose broken text layer yields only noise.
    """
    return len("".join(text.split())) >= min_chars


def _ocr_pdf_page(path: Path, page_index: int, *, dpi: int, language: str) -> str:
    """Render one PDF page and run Tesseract OCR on it."""
    try:
        with fitz.open(path) as document:
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    except Exception as exc:
        raise PdfParsingError(f"Could not render page {page_index + 1} for OCR: {exc}") from exc

    try:
        return pytesseract.image_to_string(image, lang=language).strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise PdfParsingError(
            "Tesseract OCR is not installed or is not available on PATH."
        ) from exc
    except pytesseract.TesseractError as exc:
        raise PdfParsingError(f"Tesseract OCR failed on page {page_index + 1}: {exc}") from exc


def parse_pdf(path: str | Path) -> ParsedPdfDocument:
    path = Path(path)
    try:
        reader = PdfReader(path)
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfParsingError(f"Could not open PDF '{path.name}': {exc}") from exc

    result = ParsedPdfDocument()
    settings = get_settings()

    if reader.is_encrypted:
        # Some encrypted PDFs use an empty user password (viewer restrictions
        # only); try that before giving up.
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

    ocr_pages: list[int] = []
    failed_pages: list[int] = []

    for index in range(page_count):
        page_number = index + 1
        native_text = ""

        try:
            native_text = _normalise_text(reader.pages[index].extract_text() or "")
        except Exception as exc:  # pypdf can raise a variety of parse errors per-page
            result.warnings.append(
                f"Page {page_number} native text extraction failed ({exc}); trying OCR."
            )

        if _has_usable_native_text(native_text, settings.pdf_ocr_min_native_chars):
            result.pages.append(ParsedPdfPage(number=page_number, text=native_text))
            continue

        if not settings.pdf_ocr_enabled:
            failed_pages.append(page_number)
            continue

        try:
            ocr_text = _normalise_text(
                _ocr_pdf_page(
                    path,
                    index,
                    dpi=settings.pdf_ocr_dpi,
                    language=settings.pdf_ocr_language,
                )
            )
        except PdfParsingError as exc:
            result.warnings.append(f"Page {page_number} OCR failed: {exc}")
            failed_pages.append(page_number)
            continue

        if ocr_text:
            result.pages.append(ParsedPdfPage(number=page_number, text=ocr_text))
            ocr_pages.append(page_number)
        else:
            failed_pages.append(page_number)

    if ocr_pages:
        result.warnings.append(
            f"OCR was used for {len(ocr_pages)} scanned/image-only page(s): {ocr_pages}."
        )

    if failed_pages:
        result.warnings.append(
            f"PDF '{path.name}' has {len(failed_pages)} page(s) from which no text could be "
            f"recovered: pages {failed_pages}."
        )

    if not result.pages:
        result.warnings.append(f"PDF '{path.name}' yielded no extractable or OCR text from any page.")

    return result
