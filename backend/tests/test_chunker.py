from app.ingestion.chunker import chunk_workbook
from app.ingestion.excel_parser import ParsedCell, ParsedSheet, ParsedWorkbook


def _budget_sheet() -> ParsedSheet:
    return ParsedSheet(
        name="Budget",
        is_empty=False,
        cells=[
            ParsedCell(address="A1", row=1, column=1, value="Line Item", formula=None),
            ParsedCell(address="B1", row=1, column=2, value="Q1", formula=None),
            ParsedCell(address="A2", row=2, column=1, value="Revenue", formula=None),
            ParsedCell(
                address="B2",
                row=2,
                column=2,
                value="120000",
                formula="=100000*1.2",
            ),
        ],
    )


def test_chunk_sheet_produces_one_chunk_per_data_row_and_a_summary():
    workbook = ParsedWorkbook(sheets=[_budget_sheet()])

    chunks = chunk_workbook(workbook)

    row_chunks = [c for c in chunks if c.cell_range is not None]
    summary_chunks = [c for c in chunks if c.cell_range is None]

    assert len(row_chunks) == 1
    assert "Revenue" in row_chunks[0].text
    assert "Q1=120000" in row_chunks[0].text
    assert "formula: =100000*1.2" in row_chunks[0].text
    assert row_chunks[0].sheet_name == "Budget"
    assert row_chunks[0].cell_range == "A2:B2"

    assert len(summary_chunks) == 1
    assert "Line Item" in summary_chunks[0].text
    assert "Q1" in summary_chunks[0].text


def test_empty_sheet_produces_no_chunks():
    workbook = ParsedWorkbook(sheets=[ParsedSheet(name="Template", is_empty=True, cells=[])])

    chunks = chunk_workbook(workbook)

    assert chunks == []
