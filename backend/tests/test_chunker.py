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


def _cell(address, value):
    column = ord(address[0]) - 64
    return ParsedCell(address=address, row=int(address[1:]), column=column, value=value, formula=None)


def test_title_rows_above_the_header_become_context_for_every_row():
    # A typical statement: title and units first, the real header in row 4.
    sheet = ParsedSheet(
        name="Estado de resultados",
        is_empty=False,
        cells=[
            _cell("A1", "Andina S.A.S. — Estado de resultados — Año 2024"),
            _cell("A2", "Cifras en pesos colombianos (COP)"),
            _cell("A4", "Concepto"), _cell("B4", "2024"),
            _cell("A5", "Utilidad neta"), _cell("B5", "610000"),
        ],
    )

    chunks = chunk_workbook(ParsedWorkbook(sheets=[sheet]))

    row_chunks = [c for c in chunks if c.cell_range is not None]
    assert [c.cell_range for c in row_chunks] == ["A5:B5"]
    text = row_chunks[0].text
    assert "2024=610000" in text  # the value is labelled with its period column
    assert "Año 2024" in text and "(COP)" in text  # title and units travel with the row
    summary = next(c for c in chunks if c.cell_range is None)
    assert "Concepto, 2024" in summary.text


def test_title_merged_across_columns_is_not_taken_as_the_header():
    sheet = ParsedSheet(
        name="Income",
        is_empty=False,
        cells=[
            _cell("A1", "Income Statement FY2024"), _cell("B1", "Income Statement FY2024"),
            _cell("A2", "Line Item"), _cell("B2", "Amount"),
            _cell("A3", "Revenue"), _cell("B3", "850000"),
        ],
    )

    chunks = chunk_workbook(ParsedWorkbook(sheets=[sheet]))

    row = next(c for c in chunks if c.cell_range == "A3:B3")
    assert "Amount=850000" in row.text and "FY2024" in row.text


def test_single_column_sheet_keeps_the_first_row_as_header():
    sheet = ParsedSheet(
        name="Notes",
        is_empty=False,
        cells=[_cell("A1", "Note"), _cell("A2", "Figures are audited")],
    )

    chunks = chunk_workbook(ParsedWorkbook(sheets=[sheet]))

    assert [c.cell_range for c in chunks if c.cell_range] == ["A2:A2"]
    assert "Note=Figures are audited" in chunks[0].text


def test_empty_sheet_produces_no_chunks():
    workbook = ParsedWorkbook(sheets=[ParsedSheet(name="Template", is_empty=True, cells=[])])

    chunks = chunk_workbook(workbook)

    assert chunks == []
