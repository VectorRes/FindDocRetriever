import pytest

from app.ingestion.pdf_parser import PdfParsingError, parse_pdf
from tests.pdf_helpers import make_pdf_bytes


def test_extracts_text_per_page(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(make_pdf_bytes(["Revenue grew 12 percent in Q3.", "Second page notes."]))

    result = parse_pdf(path)

    assert [p.number for p in result.pages] == [1, 2]
    assert "Revenue grew 12 percent" in result.pages[0].text
    assert "Second page notes" in result.pages[1].text


def test_page_with_no_native_text_uses_ocr_fallback(tmp_path, monkeypatch):
    path = tmp_path / "mixed.pdf"
    path.write_bytes(make_pdf_bytes(["Has text here.", ""]))

    def fake_ocr(_path, page_index, *, dpi, language):
        assert page_index == 1
        assert dpi == 300
        assert "eng" in language
        return "Scanned page recovered by OCR.", 0.92

    monkeypatch.setattr("app.ingestion.pdf_parser._ocr_pdf_page", fake_ocr)

    result = parse_pdf(path)

    assert [p.number for p in result.pages] == [1, 2]
    assert result.pages[1].text == "Scanned page recovered by OCR."
    assert any("OCR was used" in w for w in result.warnings)
    assert any("[2]" in w for w in result.warnings)


def test_native_text_page_is_tagged_with_high_confidence(tmp_path):
    path = tmp_path / "digital.pdf"
    path.write_bytes(make_pdf_bytes(["Machine-readable financial statement."]))

    result = parse_pdf(path)

    page = result.pages[0]
    assert page.extraction_method == "native"
    assert page.confidence == 1.0
    assert page.confidence_label == "native text"


def test_ocr_text_is_tagged_with_lower_confidence_than_native(tmp_path, monkeypatch):
    path = tmp_path / "mixed.pdf"
    path.write_bytes(make_pdf_bytes(["Has text here.", ""]))

    monkeypatch.setattr(
        "app.ingestion.pdf_parser._ocr_pdf_page",
        lambda *_a, **_k: ("Scanned page recovered by OCR.", 0.88),
    )

    result = parse_pdf(path)

    native_page, ocr_page = result.pages
    assert native_page.extraction_method == "native"
    assert ocr_page.extraction_method == "ocr"
    assert ocr_page.confidence < native_page.confidence
    assert ocr_page.confidence == 0.88


def test_low_confidence_ocr_is_flagged_for_verification(tmp_path, monkeypatch):
    path = tmp_path / "poor_scan.pdf"
    path.write_bytes(make_pdf_bytes([""]))

    monkeypatch.setattr(
        "app.ingestion.pdf_parser._ocr_pdf_page",
        lambda *_a, **_k: ("gr41ny sm3ared txt", 0.35),
    )

    result = parse_pdf(path)

    page = result.pages[0]
    assert page.extraction_method == "ocr"
    assert page.confidence == 0.35
    assert page.confidence_label == "low confidence — verify against scan"
    assert any("low confidence — verify against scan" in w for w in result.warnings)


def test_high_confidence_ocr_is_not_flagged_for_verification(tmp_path, monkeypatch):
    path = tmp_path / "clean_scan.pdf"
    path.write_bytes(make_pdf_bytes([""]))

    monkeypatch.setattr(
        "app.ingestion.pdf_parser._ocr_pdf_page",
        lambda *_a, **_k: ("Crisp scanned text.", 0.95),
    )

    result = parse_pdf(path)

    page = result.pages[0]
    assert page.confidence_label != "low confidence — verify against scan"
    assert not any("verify against scan" in w for w in result.warnings)


def test_average_word_confidence_ignores_non_text_elements():
    from app.ingestion.pdf_parser import _average_word_confidence

    # Tesseract reports -1 confidence for layout elements (blocks/lines) that
    # aren't actual recognised words; those must not drag the average down.
    ocr_data = {"conf": [-1, "-1", 90, 80, 70]}

    assert _average_word_confidence(ocr_data) == 0.8


def test_native_text_page_does_not_run_ocr(tmp_path, monkeypatch):
    path = tmp_path / "digital.pdf"
    path.write_bytes(make_pdf_bytes(["Machine-readable financial statement."]))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("OCR must not run for a normal text PDF page")

    monkeypatch.setattr("app.ingestion.pdf_parser._ocr_pdf_page", fail_if_called)

    result = parse_pdf(path)

    assert [p.number for p in result.pages] == [1]
    assert "Machine-readable financial statement" in result.pages[0].text


def test_encrypted_pdf_with_empty_user_password_is_decrypted(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from pypdf import PdfReader, PdfWriter

    plain_path = tmp_path / "plain.pdf"
    plain_path.write_bytes(make_pdf_bytes(["Confidential Q3 results."]))

    reader = PdfReader(plain_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password="", owner_password="ownersecret")

    enc_path = tmp_path / "encrypted.pdf"
    with open(enc_path, "wb") as f:
        writer.write(f)

    result = parse_pdf(enc_path)

    assert any("encrypted" in w.lower() for w in result.warnings)
    assert result.pages, "expected the page to be recovered via the empty user password"
    assert "Confidential Q3 results" in result.pages[0].text


def test_encrypted_pdf_with_real_password_cannot_be_parsed(tmp_path):
    from pypdf import PdfReader, PdfWriter

    plain_path = tmp_path / "plain.pdf"
    plain_path.write_bytes(make_pdf_bytes(["Secret content."]))

    reader = PdfReader(plain_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password="secret", owner_password="ownersecret")

    enc_path = tmp_path / "encrypted.pdf"
    with open(enc_path, "wb") as f:
        writer.write(f)

    result = parse_pdf(enc_path)

    assert result.pages == []
    assert any("password-protected" in w for w in result.warnings)


def test_corrupted_file_raises_clear_error(tmp_path):
    path = tmp_path / "corrupted.pdf"
    path.write_bytes(b"not a real pdf file")

    with pytest.raises(PdfParsingError):
        parse_pdf(path)


def _make_financial_table_pdf(path):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    rows = [
        ["Balance Sheet", "2024", "2023"],
        ["Cash and cash equivalents", "1,250", "1,100"],
        ["Accounts receivable", "2,400", "2,100"],
        ["Total assets", "3,650", "3,200"],
    ]
    for row_index, row in enumerate(rows):
        y = 90 + row_index * 25
        for x, value in zip((50, 350, 450), row):
            page.insert_text((x, y), value, fontsize=10)
    page.insert_text((50, 220), "Note 1: General information", fontsize=10)
    page.insert_text((50, 245), "1. Accounting policies", fontsize=10)
    doc.save(path)
    doc.close()


def test_extracts_financial_table_with_period_columns_and_notes(tmp_path):
    path = tmp_path / "balance_sheet.pdf"
    _make_financial_table_pdf(path)

    result = parse_pdf(path)

    assert len(result.tables) == 1
    table = result.tables[0]
    assert table.headers == ["Balance Sheet", "2024", "2023"]
    assert ["Cash and cash equivalents", "1,250", "1,100"] in table.rows
    assert table.confidence_label == "high-confidence structure"

    assert any(r.reference_type == "note" and r.reference_number == "1" for r in result.references)
    assert any(r.reference_type == "note" and r.reference_number == "1" and r.title == "Accounting policies" for r in result.references)


def test_financial_table_without_detectable_structure_is_flagged(tmp_path):
    path = tmp_path / "irregular.pdf"
    path.write_bytes(make_pdf_bytes([
        "Assets 2024 2023\nCash 100 90\nReceivables 200 180\n"
    ]))

    result = parse_pdf(path)

    assert result.tables == []
    assert any("low-confidence structure" in warning for warning in result.warnings)
    assert any("raw page text is retained" in warning for warning in result.warnings)
