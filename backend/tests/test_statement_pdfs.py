"""PDF financial statements in several layouts, read end to end: PyMuPDF text
layout -> app parser -> table-row chunks. Every figure must carry the column
(period) it belongs to, whatever the language or layout (see statement_pdfs.py).
"""
import pytest

from app.figures.extraction import column_of, coverage_of, same_period
from app.ingestion.chunker import chunk_pdf
from app.ingestion.pdf_parser import parse_pdf
from tests.statement_pdfs import CORPUS, write_statements


def _rows(tmp_path, name) -> dict[str, str]:
    document = parse_pdf(write_statements(tmp_path / f"{name}.pdf", [CORPUS[name]]))
    rows = {}
    for chunk in chunk_pdf(document):
        if chunk.content_type == "table_row":
            row = chunk.text[chunk.text.find(", row '") + 7:]
            label, _, values = row.partition("': ")
            rows[label] = values
    return rows


def _row(rows: dict[str, str], label_part: str) -> str:
    matches = [values for label, values in rows.items() if label_part in label]
    assert matches, f"no row containing {label_part!r} in {list(rows)}"
    return matches[0]


def test_spanish_balance_with_note_column(tmp_path):
    rows = _rows(tmp_path, "spanish_balance")

    cash = _row(rows, "Efectivo y equivalentes")
    assert cash == "31 de diciembre de 2025=12.450; 31 de diciembre de 2024=9.870"  # note "5" is not a value
    assert _row(rows, "Total pasivos corrientes") == "31 de diciembre de 2025=21.000; 31 de diciembre de 2024=19.200"
    assert any(label.startswith("Activos corrientes: ") for label in rows)  # rows carry their section
    assert coverage_of(column_of("12.450", f"x: {cash}")) == "point_in_time"


def test_spanish_income_statement_with_stacked_periods(tmp_path):
    rows = _rows(tmp_path, "spanish_income_stacked")

    net_income = _row(rows, "Utilidad neta")
    assert net_income.startswith("Tres meses terminados el 30 de junio 2026=5.040; Tres meses terminados el 30 de junio 2025=4.150")
    assert "Seis meses terminados el 30 de junio 2026=9.660" in net_income
    quarter = column_of("5.040", f"x: {net_income}")
    semester = column_of("9.660", f"x: {net_income}")
    assert coverage_of(quarter) == "three_months" and coverage_of(semester) == "six_months"


def test_english_statement_with_oldest_column_first(tmp_path):
    rows = _rows(tmp_path, "english_oldest_first")

    assert _row(rows, "Profit for the year") == "Year ended 2024=14,820; Year ended 2025=17,960"
    assert coverage_of("Year ended 2025") == "twelve_months"


def test_single_column_statement(tmp_path):
    rows = _rows(tmp_path, "single_column")

    assert _row(rows, "Inventories") == "As of December 31, 2025=2,980"
    assert "Current assets: Inventories" in rows  # the title's "(in USD thousands)" isn't part of it


@pytest.mark.parametrize(
    ("a", "b"),
    [("31 de diciembre de 2025", "FY2025"), ("31 December 2025", "2025"), ("30 de junio de 2026", "Q2 2026"),
     ("Tres meses terminados el 30 de junio de 2026", "June 30, 2026")],
)
def test_period_names_across_languages(a, b):
    assert same_period(a, b)


# --- extraction prefers the labelled table row over unlabelled page text ----------------

import re  # noqa: E402
import uuid  # noqa: E402

import app.figures.extraction as extraction_module  # noqa: E402
from app.figures.extraction import LLMFigure, extract_figure, labelled_source  # noqa: E402
from app.ingestion.service import ingest_pdf_file  # noqa: E402
from app.retrieval.service import RetrievedChunk  # noqa: E402


def _chunk(text, page=1):
    return RetrievedChunk(document_id="d", document_filename="f.pdf", sheet_name=None, cell_range=None,
                          page_number=page, text=text)


PAGE_TEXT = "ESTADO DE RESULTADOS Tres meses terminados Seis meses terminados 2026 2025 2026 2025 Utilidad neta 5.040 4.150 9.660 7.980"
NET_INCOME_ROW = ("Financial table page 1 (…), row 'Utilidad neta': Tres meses terminados el 30 de junio 2026=5.040; "
                  "Tres meses terminados el 30 de junio 2025=4.150")
OTHER_ROW = "Financial table page 1 (…), row 'Dividendos': Tres meses terminados el 30 de junio 2026=5.040"


def test_labelled_row_replaces_unlabelled_page_text():
    sources = {"S1": _chunk(PAGE_TEXT), "S2": _chunk(NET_INCOME_ROW)}
    assert labelled_source(sources, "S1", "5.040", "Utilidad neta")[0] == "S2"


def test_several_rows_with_the_figure_are_told_apart_by_label_or_left_alone():
    sources = {"S1": _chunk(PAGE_TEXT), "S2": _chunk(NET_INCOME_ROW), "S3": _chunk(OTHER_ROW)}
    assert labelled_source(sources, "S1", "5.040", "Utilidad neta")[0] == "S2"
    # Same column in both rows: the period is clear even without telling the rows apart.
    assert labelled_source(sources, "S1", "5.040", None) is not None
    semester = "Financial table page 1 (…), row 'Dividendos': Seis meses terminados el 30 de junio 2026=5.040"
    mixed = {"S1": _chunk(PAGE_TEXT), "S2": _chunk(NET_INCOME_ROW), "S3": _chunk(semester)}
    assert labelled_source(mixed, "S1", "5.040", None) is None  # different columns: ambiguous


def test_a_labelled_citation_is_kept():
    sources = {"S1": _chunk(NET_INCOME_ROW), "S2": _chunk(OTHER_ROW)}
    assert labelled_source(sources, "S1", "5.040", "Utilidad neta") is None


class _CitesPageText:
    """Plays a model that cites the page text (no column labels) for the figure."""

    def with_structured_output(self, schema):
        class _Structured:
            def invoke(self, messages):
                prompt = messages[-1]["content"]
                for match in re.finditer(r"\[(S\d+)\][^\n]*\n([^\[]*)", prompt):
                    if "row '" not in match.group(2) and "5.040" in match.group(2):
                        return LLMFigure(found=True, source_id=match.group(1), value_text="5.040",
                                         label="Utilidad neta", period="2026")
                return LLMFigure(found=False)
        return _Structured()


def test_extraction_cites_the_labelled_row_end_to_end(monkeypatch, db_session, tmp_path):
    path = write_statements(tmp_path / f"{uuid.uuid4().hex}.pdf", [CORPUS["spanish_income_stacked"]])
    document = ingest_pdf_file(db_session, filename="EERR_Constructora.pdf", file_path=path)
    document.confidentiality_tag = "public"
    for chunk in document.chunks:
        chunk.confidentiality_tag = "public"
    db_session.commit()
    monkeypatch.setattr(extraction_module, "get_chat_model", lambda: _CitesPageText())
    try:
        figure = extract_figure(db_session, document.id, "net income / utilidad neta")
        assert figure.found
        assert "row 'Utilidad neta'" in figure.citation.text
        assert figure.column_header == "Tres meses terminados el 30 de junio 2026"
        assert figure.period_coverage == "three_months"
    finally:
        db_session.delete(document)
        db_session.commit()


# --- more layouts: fiscal years, footnotes and note references, scanned pages -------------

def test_fiscal_year_columns(tmp_path):
    rows = _rows(tmp_path, "fiscal_year")

    net_income = _row(rows, "Net income")
    assert net_income == "Years Ended September 27, 2025=104,800; Years Ended September 28, 2024=93,736"
    assert coverage_of("Years Ended September 27, 2025") == "twelve_months"


def test_note_reference_is_not_glued_to_the_figure(tmp_path):
    # "Revenue (1)  4  58,240": the "4" is a note, not the start of 458,240.
    rows = _rows(tmp_path, "footnotes_thousands")

    assert _row(rows, "Revenue (1)") == "year ended 2025=58,240; year ended 2024=52,915"
    assert _row(rows, "Cost of services") == "year ended 2025=(39,600); year ended 2024=(36,020)"


def test_scanned_statement_gives_no_mislabelled_figures(tmp_path):
    from tests.statement_pdfs import SPANISH_BALANCE, write_scanned

    document = parse_pdf(write_scanned(tmp_path / "scanned.pdf", SPANISH_BALANCE))
    chunks = chunk_pdf(document)

    assert document.pages[0].extraction_method == "ocr"
    # OCR text only: no table rows that could put a figure under the wrong column.
    assert not [c for c in chunks if c.content_type == "table_row"]
    assert any("Efectivo" in c.text for c in chunks if c.content_type == "text")
