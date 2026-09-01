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


def test_ocr_page_chunks_carry_lower_confidence_than_native_page_chunks():
    document = ParsedPdfDocument(
        pages=[
            ParsedPdfPage(number=1, text="Native page text."),
            ParsedPdfPage(
                number=2,
                text="Scanned page text.",
                extraction_method="ocr",
                confidence=0.4,
                confidence_label="low confidence — verify against scan",
            ),
        ]
    )

    chunks = chunk_pdf(document, chunk_size=1000, chunk_overlap=150)
    native_chunk = next(c for c in chunks if c.page_number == 1)
    ocr_chunk = next(c for c in chunks if c.page_number == 2)

    assert native_chunk.extraction_method == "native"
    assert native_chunk.confidence == 1.0
    assert ocr_chunk.extraction_method == "ocr"
    assert ocr_chunk.confidence == 0.4
    assert ocr_chunk.confidence_label == "low confidence — verify against scan"
    assert ocr_chunk.confidence < native_chunk.confidence


def test_empty_page_produces_no_chunks():
    document = ParsedPdfDocument(pages=[ParsedPdfPage(number=1, text="")])

    assert chunk_pdf(document) == []


def test_split_into_windows_never_splits_a_word_and_always_progresses():
    windows = _split_into_windows("a" * 500, chunk_size=100, chunk_overlap=20)

    assert windows == ["a" * 500]


def test_split_into_windows_empty_text():
    assert _split_into_windows("", chunk_size=100, chunk_overlap=20) == []


def test_table_rows_become_structured_retrieval_chunks():
    from app.ingestion.pdf_parser import ParsedPdfTable

    document = ParsedPdfDocument(
        pages=[ParsedPdfPage(number=3, text="Balance sheet")],
        tables=[
            ParsedPdfTable(
                page_number=3,
                table_index=1,
                headers=["Line item", "2024", "2023"],
                rows=[["Revenue", "1,250", "1,100"]],
                confidence=0.95,
                confidence_label="high-confidence structure",
                raw_text="Line item | 2024 | 2023\nRevenue | 1,250 | 1,100",
            )
        ],
    )

    chunks = chunk_pdf(document)
    structured = [c for c in chunks if c.content_type == "table_row"]

    assert len(structured) == 1
    assert "Revenue" in structured[0].text
    assert "2024=1,250" in structured[0].text
    assert "2023=1,100" in structured[0].text
    assert structured[0].page_number == 3
