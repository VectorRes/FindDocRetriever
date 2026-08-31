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
        return "Scanned page recovered by OCR."

    monkeypatch.setattr("app.ingestion.pdf_parser._ocr_pdf_page", fake_ocr)

    result = parse_pdf(path)

    assert [p.number for p in result.pages] == [1, 2]
    assert result.pages[1].text == "Scanned page recovered by OCR."
    assert any("OCR was used" in w for w in result.warnings)
    assert any("[2]" in w for w in result.warnings)


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
