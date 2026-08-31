from app.ingestion.chunker import _split_into_windows, chunk_pdf
from app.ingestion.pdf_parser import ParsedPdfDocument, ParsedPdfPage


def test_short_page_produces_a_single_chunk():
    document = ParsedPdfDocument(pages=[ParsedPdfPage(number=1, text="Revenue grew 12 percent in Q3.")])

    chunks = chunk_pdf(document, chunk_size=1000, chunk_overlap=150)

    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert chunks[0].sheet_name is None
    assert chunks[0].cell_range is None
    assert "Revenue grew 12 percent" in chunks[0].text


def test_long_page_split_into_overlapping_chunks_tagged_with_page_number():
    text = " ".join(f"word{i}" for i in range(300))
    document = ParsedPdfDocument(pages=[ParsedPdfPage(number=4, text=text)])

    chunks = chunk_pdf(document, chunk_size=100, chunk_overlap=20)

    assert len(chunks) > 1
    assert all(chunk.page_number == 4 for chunk in chunks)
    # Consecutive chunks should share some trailing/leading words (the overlap).
    first_words = chunks[0].text.split()
    second_words = chunks[1].text.split()
    assert set(first_words[-3:]) & set(second_words[:3])


def test_empty_page_produces_no_chunks():
    document = ParsedPdfDocument(pages=[ParsedPdfPage(number=1, text="")])

    assert chunk_pdf(document) == []


def test_split_into_windows_never_splits_a_word_and_always_progresses():
    windows = _split_into_windows("a" * 500, chunk_size=100, chunk_overlap=20)

    assert windows == ["a" * 500]


def test_split_into_windows_empty_text():
    assert _split_into_windows("", chunk_size=100, chunk_overlap=20) == []
