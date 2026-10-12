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


# --- PDF tables: column headers recovered, currency-sign columns ignored -----------------

from app.ingestion.chunker import _table_row_chunks  # noqa: E402
from app.ingestion.pdf_parser import ParsedPdfTable  # noqa: E402


def _pdf_table(headers, rows, page=5):
    return ParsedPdfTable(page_number=page, table_index=1, headers=headers, rows=rows,
                          confidence=0.7, confidence_label="table", raw_text="")


BALANCE_PAGE = ("Alphabet Inc. CONSOLIDATED BALANCE SHEETS (In millions, except per share amounts) "
                "As of December 31, 2025 As of June 30, 2026 (unaudited) Assets Current assets: "
                "Cash and cash equivalents $ 30,708 $ 55,911 Total current assets 206,038 343,524")
INCOME_PAGE = ("Alphabet Inc. CONSOLIDATED STATEMENTS OF INCOME (In millions, unaudited) Quarter Ended June 30, "
               "Year To Date June 30, 2025 2026 2025 2026 Revenues $ 96,428 $ 119,796 $ 186,662 $ 229,692")


def test_balance_sheet_columns_take_the_dates_above_the_table():
    # pdfplumber split "Assets" into header fragments and lost the real headers.
    table = _pdf_table(["Ass", "sets", "", "", "", ""], [
        ["Cash and cash equivalents", "", "$", "30,708", "$", "55,911"],
        ["Total current assets", "", "", "206,038", "", "343,524"],
        ["$0.001 par value per share", "", "", "", "", ""],
    ])

    texts = [c.text for c in _table_row_chunks(table, BALANCE_PAGE)]

    assert "row 'Total current assets': As of December 31, 2025=206,038; As of June 30, 2026=343,524" in texts[1]
    assert "(In millions" in texts[1]  # units travel with the row
    assert "=$" not in texts[0]


def test_income_statement_columns_combine_period_groups_and_years():
    # A "$" column shifts each year header one column left of its value.
    table = _pdf_table(["", "2025", "", "", "2026", "", "2025", "", "2026"], [
        ["Revenues", "$", "96,428", "$", "119,796", "$", "186,662", "$", "229,692"],
        ["Net income", "", "28,196", "", "112,193", "", "62,736", "", "174,771"],
    ], page=6)

    [revenues, net_income] = [c.text for c in _table_row_chunks(table, INCOME_PAGE)]

    assert ("row 'Net income': Quarter Ended June 30 2025=28,196; Quarter Ended June 30 2026=112,193; "
            "Year To Date June 30 2025=62,736; Year To Date June 30 2026=174,771") in net_income
    assert "=$" not in revenues


def test_currency_sign_glued_to_a_value_is_removed():
    table = _pdf_table(["Operating activities", "", "", "", ""], [
        ["Net income", "28,196 $", "112,193 $", "62,736 $", "174,771"],
    ], page=7)
    page = "CONSOLIDATED STATEMENTS OF CASH FLOWS (In millions) Quarter Ended June 30, Year To Date June 30, 2025 2026 2025 2026 Operating activities Net income"

    [row] = [c.text for c in _table_row_chunks(table, page)]

    assert "Quarter Ended June 30 2026=112,193" in row and "$" not in row.split("row ")[1]


def test_table_without_context_keeps_its_own_headers():
    table = _pdf_table(["Line item", "Q1", "Q2"], [["Revenue", "100", "120"]])

    [row] = [c.text for c in _table_row_chunks(table, "")]

    assert row == "Financial table page 5, row 'Revenue': Q1=100; Q2=120"


def test_figures_split_across_cells_are_joined():
    table = _pdf_table(["", "2025", "", "", "2026"], [
        ["Google Search & other", "$", "5", "4,190", "63,271"],
        ["Number of employees", "1", "8", "7,103", "198,933"],
    ], page=2)

    texts = [c.text for c in _table_row_chunks(table, "")]

    assert "=54,190" in texts[0] and "=4,190" not in texts[0]
    assert "=187,103" in texts[1]


def test_year_row_inside_the_table_is_the_header_and_text_above_is_dropped():
    # pdfplumber swept the page's bullet text into the table, then the real header.
    table = _pdf_table(["results for the", "quarter", "ended Ju", "ne 30, 2026.", "", ""], [
        ["Consolidated revenues increased 24%", "", "", "", "9", ".8 bill"],
        ["", "", "", "2025", "", "2026"],
        ["Diluted net income per common share", "", "", "", "2.31", "9.11"],
    ], page=1)

    texts = [c.text for c in _table_row_chunks(table, "")]

    assert texts == ["Financial table page 1, row 'Diluted net income per common share': 2025=2.31; 2026=9.11"]


def test_partial_or_mixed_headers_are_not_used():
    # A sentence fragment over one column only would label a 2025 value "June 30, 2026".
    partial = _pdf_table(["quarter", "ne 30, 2026.", "", ""], [["EPS", "2.31", "", "9.11"]])
    mixed = _pdf_table(["", "2025", "orte", "ct Reven"], [["Revenues", "96,428", "24", "23"]])

    [p] = [c.text for c in _table_row_chunks(partial, "")]
    [m] = [c.text for c in _table_row_chunks(mixed, "")]

    assert p.endswith("row 'EPS': column 1=2.31; column 2=9.11")
    assert m.endswith("row 'Revenues': column 1=96,428; column 2=24; column 3=23")
