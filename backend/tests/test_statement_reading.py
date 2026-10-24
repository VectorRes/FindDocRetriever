"""Reading figures from any statement layout, not one document's quirks.

Each test covers a mechanism that made ratios wrong on some real filing
(10-K/10-Q tables running oldest to newest, "$" glued to the first figure,
split digit groups, statements without a "Total liabilities" or "Gross
profit" line, company names written differently), with inputs shaped like
several layouts rather than one document.
"""
import pytest

from app.figures.extraction import (
    _reads_as_period,
    choose_column,
    known_entity,
    labelled_columns,
    same_entity,
    same_period,
    stated_scale,
)
from app.ingestion.chunker import (
    _attach_minus_signs,
    _clean_value,
    _is_data_row,
    _respace_label,
    _split_label_value,
    _table_row_chunks,
)
from app.ingestion.pdf_parser import ParsedPdfTable
from app.qa.confidence import join_split_groups, match_figure
from app.ratios.engine import MappedInput, _comparable_period, _operands, to_mapped_input
from app.ratios.formulas import LINE_ITEMS
from app.ratios.mapping import names_other_item
from tests.test_ratios import BALANCE_SHEET, INCOME_STATEMENT, _found, _run, statements  # noqa: F401


# --- PDF table rows --------------------------------------------------------------------------------

def test_figure_glued_to_the_label_goes_back_to_its_column():
    row = ["Net income $ 30,425", "", "$", "59,248", "$", "77,670"]
    assert _split_label_value(row, [1, 3, 5]) == ["Net income", "30,425", "$", "59,248", "$", "77,670"]
    # A label that only mentions a number in words, or a row whose first column has its figure, is left alone.
    assert _split_label_value(["Revenue", "100", "$", "200"], [1, 3]) == ["Revenue", "100", "$", "200"]
    assert _split_label_value(["Par value $0.01 per share", "", "5"], [1, 2])[0] == "Par value $0.01 per share"


def test_negative_in_the_last_column_keeps_its_parenthesis():
    assert _clean_value("(2,274") == "(2,274)"
    assert _clean_value("(2,274)") == "(2,274)"
    assert _clean_value("2,274") == "2,274"


def test_letter_spaced_label_is_read_as_printed():
    page = "Revenues: Google Services $ 82,543 Total revenues $ 96,428 $ 119,796"
    assert _respace_label("T o tal revenue s", page) == "Total revenues"
    assert _respace_label("Total revenues", page) == "Total revenues"
    assert _respace_label("S o mething else", page) == "S o mething else"  # not on the page: unchanged


def test_oldest_to_newest_statement_keeps_every_year():
    """A 10-K income statement: three years, "$" before the first row's figures."""
    page = ("CONSOLIDATED STATEMENTS OF OPERATIONS (in millions) Year Ended December 31, 2023 2024 2025 "
            "Total net sales 574,785 637,959 716,924 Interest expense (3,182) (2,406) (2,274) "
            "Net income $ 30,425 $ 59,248 $ 77,670")
    table = ParsedPdfTable(
        page_number=48, table_index=0, headers=["", "2023", "2024", "", "2025", ""],
        rows=[
            ["Total net sales", "574,785", "", "637,959", "", "716,924"],
            ["Interest expense", "(3,182)", "", "(2,406)", "", "(2,274"],
            ["Net income $ 30,425", "", "$", "59,248", "$", "77,670"],
        ],
        confidence=0.9, confidence_label="high", raw_text=page,
    )
    rows = {chunk.text.split("row '")[1].split("'")[0]: chunk.text for chunk in _table_row_chunks(table, page)}
    assert "2023=30,425" in rows["Net income"] and "2025=77,670" in rows["Net income"]
    assert "2025=(2,274)" in rows["Interest expense"]


# --- numbers ---------------------------------------------------------------------------------------

def test_digit_group_split_by_a_space_is_one_figure():
    assert join_split_groups("Operating income $ 79 ,975") == "Operating income $ 79,975"
    assert match_figure("$ 79 ,975", "Operating Income $ 79 ,975 $ 68,593") == 79_975
    # Prose ("in 79, 975 stores") keeps the space after the comma: two numbers.
    assert join_split_groups("79, 975 stores") == "79, 975 stores"


def test_scale_comes_from_the_source_when_it_states_one():
    assert stated_scale("Financial table page 48 (CONSOLIDATED STATEMENTS (in millions, except per share data))") == "millions"
    assert stated_scale("Interest expense was $2.3 billion in 2025") == "billions"
    assert stated_scale("Cifras en pesos colombianos (COP)") is None
    assert stated_scale("In millions, except shares, which are reflected in thousands") is None  # two: undecided


def test_explicit_different_scales_in_one_document_are_compared_scaled():
    common = dict(statement="income_statement", found=True, document_id="d")
    operating = MappedInput(key="operating_income", name="Operating income", value=79_975e6, unit_scale="millions", **common)
    interest = MappedInput(key="interest_expense", name="Interest expense", value=2.3e9, unit_scale="billions", **common)
    operands = _operands([operating, interest])
    assert operands["operating_income"] / operands["interest_expense"] == pytest.approx(79_975 / 2_300)
    # One scale applied and one not ("units") is an extraction slip: as written.
    unscaled = MappedInput(key="interest_expense", name="Interest expense", value=2_274, unit_scale="units", **common)
    operands = _operands([operating, unscaled])
    assert operands["operating_income"] / operands["interest_expense"] == pytest.approx(79_975 / 2_274)


# --- which column ----------------------------------------------------------------------------------

AMAZON_ROW = ("Financial table page 48 (CONSOLIDATED STATEMENTS OF OPERATIONS (in millions)), row 'Net income': "
              "Year Ended December 31 2023=30,425; Year Ended December 31 2024=59,248; Year Ended December 31 2025=77,670")
APPLE_ROW = ("Financial table page 4 (...), row 'Total net sales': Three Months Ended June 27, 2026=109,417; "
             "Three Months Ended June 28, 2025=94,036; Nine Months Ended June 27, 2026=364,357; "
             "Nine Months Ended June 28, 2025=313,695")
ALPHABET_ROW = ("Financial table page 6 (...), row 'Revenues': Quarter Ended June 30 2025=96,428; "
                "Quarter Ended June 30 2026=119,796; Year To Date June 30 2025=186,662; Year To Date June 30 2026=229,692")
BALANCE_ROW = "Financial table page 50 (BALANCE SHEETS), row 'Total stockholders’ equity': 2024=285,970; 2025=411,065"
EXCEL_ROW = "Sheet 'Proyección', Utilidad neta: Concepto=Utilidad neta; 2024 real=610000; 2025 presupuesto=723723"


@pytest.mark.parametrize("text, period, expected", [
    (AMAZON_ROW, None, "77,670"),             # newest is the last column
    (AMAZON_ROW, "FY2024", "59,248"),
    (BALANCE_ROW, None, "411,065"),
    (BALANCE_ROW, "FY2025", "411,065"),
    (APPLE_ROW, None, "109,417"),             # newest date, the quarter before nine months
    (APPLE_ROW, "Q3 2026", "109,417"),        # fiscal quarter ending June 27: the three-month column
    (ALPHABET_ROW, None, "119,796"),          # newest is the second column; quarter before year to date
    (ALPHABET_ROW, "Q2 2026", "119,796"),
    (ALPHABET_ROW, "Q2 2025", "96,428"),
    (EXCEL_ROW, None, "610000"),              # an actual, not the budget
])
def test_column_is_chosen_from_the_row_labels(text, period, expected):
    assert choose_column(labelled_columns(text), period)[1] == expected


def test_column_is_left_to_the_model_when_labels_dont_settle_it():
    assert choose_column(labelled_columns("row 'Revenue': column 1=100; column 2=200"), None) is None
    assert choose_column(labelled_columns(BALANCE_ROW), "Q3 2026") is None  # no column for that period
    assert choose_column(labelled_columns("row 'Revenue': 2024=100"), None) is None  # one column: nothing to choose


def test_bare_year_column_is_a_period():
    net_income = MappedInput(key="net_income", name="Net income", statement="income_statement", found=True,
                             column_header="Year Ended December 31 2023")
    total_assets = MappedInput(key="total_assets", name="Total assets", statement="balance_sheet", found=True,
                               column_header="2025")
    assert _comparable_period(net_income) == "December 31 2023"
    assert _comparable_period(total_assets) == "2025"


# --- companies -------------------------------------------------------------------------------------

def test_company_names_written_differently_are_one_company():
    assert same_entity("Amazon", "AMAZON.COM, INC.")
    assert same_entity("Andina", "Andina S.A.S.")
    assert not same_entity("Apple Inc.", "Alphabet Inc.")
    assert known_entity("Not specified") is None and known_entity("unknown") is None
    assert known_entity("Pacífico S.A.") == "Pacífico S.A."


# --- line items ------------------------------------------------------------------------------------

@pytest.mark.parametrize("key, label", [
    ("gross_profit", "Operating income"),
    ("gross_profit", "Total income from operations"),
    ("total_liabilities", "Total liabilities and stockholders’ equity"),
    ("total_liabilities", "Total pasivos y patrimonio"),
    ("revenue", "Net income"),
])
def test_another_line_item_is_not_this_one(key, label):
    assert names_other_item(LINE_ITEMS[key], label)


@pytest.mark.parametrize("key, label", [
    ("current_liabilities", "Short-Term Obligations"),       # a non-standard name for it: review, not reject
    ("accounts_receivable", "Accounts receivable, net and other"),
    ("revenue", "Total net sales"),                          # its own name
])
def test_non_standard_names_are_not_other_items(key, label):
    assert not names_other_item(LINE_ITEMS[key], label)


def test_another_line_item_is_reported_as_not_found(statements):
    income, _ = statements
    mapped = to_mapped_input(LINE_ITEMS["gross_profit"], income, _found(income, "Operating income", 964_000))
    assert not mapped.found and "different item" in mapped.reason


def test_missing_total_liabilities_is_derived_from_assets_and_equity(statements):
    income, balance = statements
    balance_sheet = {k: v for k, v in BALANCE_SHEET.items() if k != "total_liabilities"}
    report, ratios, _ = _run({income: INCOME_STATEMENT, balance: balance_sheet}, [income, balance],
                             ratio_keys=["debt_to_equity", "debt_ratio"])
    debt_to_equity = ratios["debt_to_equity"]
    assert debt_to_equity.status == "calculated"
    assert debt_to_equity.value == pytest.approx((6_100_000 - 2_440_000) / 2_440_000)
    assert "derived as Total assets − Total equity" in " ".join(debt_to_equity.reasons)
    # Both lines it comes from are cited.
    cited = " ".join(c.text for c in ratios["debt_ratio"].citations)
    assert "Total assets" in cited and "Total equity" in cited


def test_missing_gross_profit_is_derived_from_revenue_and_cost_of_sales(statements):
    income, balance = statements
    income_statement = {k: v for k, v in INCOME_STATEMENT.items() if k != "gross_profit"}
    _, ratios, _ = _run({income: income_statement, balance: BALANCE_SHEET}, [income, balance],
                        ratio_keys=["gross_margin"])
    assert ratios["gross_margin"].status == "calculated"
    assert ratios["gross_margin"].value == pytest.approx((4_820_000 - 2_892_000) / 4_820_000)


def test_no_derivation_from_different_periods(statements):
    income, balance = statements
    balance_sheet = {k: v for k, v in BALANCE_SHEET.items() if k != "total_liabilities"}
    _, ratios, _ = _run({income: INCOME_STATEMENT, balance: balance_sheet}, [income, balance],
                        ratio_keys=["debt_to_equity"], periods={"total_assets": "FY2025"})
    assert ratios["debt_to_equity"].status == "not_calculable"


# --- comparative statements outside the US (found on a Colombian IFRS statement) ------------------

def test_every_figure_glued_to_a_total_goes_back_to_its_column():
    row = ["Total activo corriente $5,952,398 $768,466 $5,183,932", "", "", "", "674.58%"]
    assert _split_label_value(row, [1, 2, 3, 4]) == [
        "Total activo corriente", "5,952,398", "768,466", "5,183,932", "674.58%"]
    assert _split_label_value(["Total Egresos $949,813 -$6,081,160", "", ""], [1, 2])[1:] == ["949,813", "-6,081,160"]


def test_minus_sign_in_its_own_cell_belongs_to_the_next_figure():
    assert _attach_minus_signs(["Anticipos", "560,259", "-", "36,977"], [1, 3]) == ["Anticipos", "560,259", "", "-36,977"]
    assert _attach_minus_signs(["Deudores", "289", "499 -", "209"], [1, 2, 3]) == ["Deudores", "289", "499", "-209"]
    # A dash in a value column is a nil value, not a sign.
    assert _attach_minus_signs(["Patrimonios", "72,352", "-"], [1, 2]) == ["Patrimonios", "72,352", "-"]


def test_numeric_dates_and_change_columns_label_the_columns():
    page = ("ESTADO DE SITUACIÓN FINANCIERA (Cifras expresadas en miles de pesos colombianos) "
            "31/12/2024 31/12/2023 Variación absoluta Variación Relativa Activo corriente "
            "Caja y bancos 6.1 74,155 45,555 28,600 62.78% Fondo rotatorio 6.2 5,314,131 83,325 5,230,805 6277.56% "
            "Anticipos 6.3 560,259 597,236 - 36,977 -6.60%")
    table = ParsedPdfTable(
        page_number=2, table_index=0,
        headers=["COMPARATIVO AL", "", "31", "DE DICIEMBRE DE 20", "24 Y 31 DE DICIE", "023"],
        rows=[
            ["Caja y bancos", "6.1", "74,155", "45,555", "28,600", "62.78%"],
            ["Fondo rotatorio", "6.2", "5,314,131", "83,325", "5,230,805", "6277.56%"],
            ["Anticipos", "6.3", "560,259", "597,236", "36,977", "-6.60%"],
        ],
        confidence=0.9, confidence_label="high", raw_text=page,
    )
    text = _table_row_chunks(table, page)[0].text
    # The note reference column (6.1, 6.2, 6.3) isn't a value; the split title isn't a data row.
    assert "31/12/2024=74,155; 31/12/2023=45,555; Variación absoluta=28,600" in text
    assert choose_column(labelled_columns(text), None)[1] == "74,155"        # never the change column
    assert choose_column(labelled_columns(text), "FY2023")[1] == "45,555"


def test_a_split_title_is_not_a_data_row_but_small_figures_are():
    assert not _is_data_row(["COMPARATIVO AL", "", "31", "DE DICIEMBRE DE 20", "023"])
    assert _is_data_row(["Products", "47,153", "43,620"])
    assert _is_data_row(["Revenue", "471", "436"])
    assert not _is_data_row(["", "2025", "2024"])


def test_numeric_dates_are_the_same_period_as_their_names():
    assert same_period("31/12/2024", "FY2024")
    assert same_period("31/12/2024", "Q4 2024")
    assert same_period("06/30/2026", "Q2 2026")       # month first
    assert not same_period("31/12/2023", "FY2024")


def test_rows_with_mangled_period_headings_are_not_trusted_first():
    assert _reads_as_period("Three Months Ended June 27, 2026")
    assert _reads_as_period("31/12/2024")
    assert _reads_as_period("Año 2024")
    assert not _reads_as_period("years ended mber")


def test_a_percentage_is_not_an_amount(statements):
    income, _ = statements
    figure = _found(income, "%", 27)
    figure.value_text = "27 %"
    assert not to_mapped_input(LINE_ITEMS["gross_profit"], income, figure).found


def test_balance_read_from_the_cash_flow_statement_is_held(statements):
    _, balance = statements
    figure = _found(balance, "Cuentas por Cobrar", 37_187)
    figure.citation.text = ("Financial table page 5 (ESTADO DE FLUJO DE EFECTIVO (Cifras en miles)), "
                            "row 'Cuentas por Cobrar': 31/12/2024=37,187; 31/12/2023=(490)")
    mapped = to_mapped_input(LINE_ITEMS["accounts_receivable"], balance, figure)
    assert mapped.coverage_mismatch and "cash flow statement" in mapped.reason
