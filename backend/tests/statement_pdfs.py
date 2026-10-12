"""Realistic financial-statement PDFs in several layouts, for testing PDF table
reading end to end (PyMuPDF text layout -> app parser -> chunks).

Each statement is a title block followed by right-aligned figure columns, the
way statements are typeset; nothing here is tailored to one issuer.
"""
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

LABEL_X = 50
FONT_SIZE = 9
LINE = 13


@dataclass
class Statement:
    title: list[str]                       # centred lines above the table
    headings: list[list[str]]              # header rows, one cell per figure column ("" = none)
    rows: list[tuple[str, list[str]]]      # (label, figures); a label ending in ":" is a section
    columns: list[float] = field(default_factory=lambda: [330, 420, 510])  # right edge of each column
    first_column_header: str = ""          # e.g. "Nota" heading over the first figure column


def _right(page: fitz.Page, text: str, right: float, y: float, bold: bool = False) -> None:
    font = "hebo" if bold else "helv"
    width = fitz.get_text_length(text, fontname=font, fontsize=FONT_SIZE)
    page.insert_text((right - width, y), text, fontname=font, fontsize=FONT_SIZE)


def write_statements(path: Path, statements: list[Statement]) -> Path:
    document = fitz.open()
    for statement in statements:
        page = document.new_page(width=595, height=842)
        y = 60
        for line in statement.title:
            width = fitz.get_text_length(line, fontname="hebo", fontsize=10)
            page.insert_text(((595 - width) / 2, y), line, fontname="hebo", fontsize=10)
            y += LINE
        y += LINE
        for heading in statement.headings:
            for text, right in zip(heading, statement.columns):
                if text:
                    _right(page, text, right, y, bold=True)
            y += LINE
        for label, figures in statement.rows:
            page.insert_text((LABEL_X, y), label, fontname="helv", fontsize=FONT_SIZE)
            for text, right in zip(figures, statement.columns):
                if text:
                    _right(page, text, right, y)
            y += LINE
    document.save(path)
    document.close()
    return path


# --- the corpus -------------------------------------------------------------------

SPANISH_BALANCE = Statement(
    title=["CONSTRUCTORA ANDINA S.A.S.", "ESTADO DE SITUACIÓN FINANCIERA",
           "Al 31 de diciembre de 2025 y 2024", "(Cifras expresadas en millones de pesos colombianos)"],
    headings=[["Nota", "31 de diciembre de 2025", "31 de diciembre de 2024"]],
    rows=[
        ("Activos corrientes:", []),
        ("Efectivo y equivalentes de efectivo", ["5", "12.450", "9.870"]),
        ("Cuentas comerciales por cobrar", ["6", "18.300", "16.940"]),
        ("Inventarios", ["7", "9.150", "8.020"]),
        ("Total activos corrientes", ["", "39.900", "34.830"]),
        ("Propiedades, planta y equipo", ["8", "61.100", "58.400"]),
        ("Total activos", ["", "101.000", "93.230"]),
        ("Pasivos corrientes:", []),
        ("Obligaciones financieras", ["9", "8.400", "7.900"]),
        ("Cuentas por pagar comerciales", ["10", "12.600", "11.300"]),
        ("Total pasivos corrientes", ["", "21.000", "19.200"]),
        ("Obligaciones financieras de largo plazo", ["9", "29.000", "31.500"]),
        ("Total pasivos", ["", "50.000", "50.700"]),
        ("Total patrimonio", ["11", "51.000", "42.530"]),
    ],
    columns=[300, 420, 530],
)

SPANISH_INCOME_STACKED = Statement(
    title=["CONSTRUCTORA ANDINA S.A.S.", "ESTADO DE RESULTADOS INTEGRAL",
           "Por los periodos terminados el 30 de junio de 2026 y 2025",
           "(Cifras expresadas en millones de pesos colombianos)"],
    headings=[["Tres meses terminados el 30 de junio", "", "Seis meses terminados el 30 de junio", ""],
              ["2026", "2025", "2026", "2025"]],
    rows=[
        ("Ingresos de actividades ordinarias", ["42.000", "37.500", "81.200", "72.300"]),
        ("Costo de ventas", ["(25.200)", "(23.100)", "(48.700)", "(44.800)"]),
        ("Utilidad bruta", ["16.800", "14.400", "32.500", "27.500"]),
        ("Utilidad operacional", ["8.400", "7.000", "16.100", "13.200"]),
        ("Gastos financieros", ["(1.050)", "(980)", "(2.090)", "(1.950)"]),
        ("Utilidad neta", ["5.040", "4.150", "9.660", "7.980"]),
    ],
    columns=[300, 370, 450, 520],
)

ENGLISH_OLDEST_FIRST = Statement(
    title=["NORTHWIND TRADING PLC", "CONSOLIDATED INCOME STATEMENT",
           "Year ended 31 December", "(in thousands of GBP)"],
    headings=[["2024", "2025"]],
    rows=[
        ("Revenue", ["184,200", "201,750"]),
        ("Cost of sales", ["(110,500)", "(119,040)"]),
        ("Gross profit", ["73,700", "82,710"]),
        ("Operating profit", ["21,300", "25,900"]),
        ("Finance costs", ["(2,100)", "(2,450)"]),
        ("Profit for the year", ["14,820", "17,960"]),
    ],
    columns=[420, 520],
)

SINGLE_COLUMN = Statement(
    title=["PACIFIC FOODS INC.", "BALANCE SHEET", "As of December 31, 2025", "(in USD thousands)"],
    headings=[],
    rows=[
        ("Current assets:", []),
        ("Cash and cash equivalents", ["3,410"]),
        ("Inventories", ["2,980"]),
        ("Total current assets", ["9,870"]),
        ("Total assets", ["24,500"]),
        ("Total current liabilities", ["6,120"]),
        ("Total liabilities", ["11,300"]),
        ("Total equity", ["13,200"]),
    ],
    columns=[520],
)

FISCAL_YEAR = Statement(
    title=["ORCHARD DEVICES INC.", "CONSOLIDATED STATEMENTS OF OPERATIONS", "(In millions)"],
    headings=[["Fiscal Years Ended", ""], ["September 27, 2025", "September 28, 2024"]],
    rows=[
        ("Net sales", ["412,300", "391,035"]),
        ("Cost of sales", ["228,100", "210,352"]),
        ("Gross margin", ["184,200", "180,683"]),
        ("Operating income", ["129,400", "123,216"]),
        ("Net income", ["104,800", "93,736"]),
    ],
    columns=[420, 520],
)

FOOTNOTES_THOUSANDS = Statement(
    title=["HARBOR LOGISTICS S.A.", "STATEMENT OF PROFIT OR LOSS", "For the year ended 31 December 2025",
           "(in thousands of EUR)"],
    headings=[["Note", "2025", "2024"]],
    rows=[
        ("Revenue (1)", ["4", "58,240", "52,915"]),
        ("Cost of services", ["5", "(39,600)", "(36,020)"]),
        ("Operating profit (2)", ["", "9,870", "8,410"]),
        ("Finance costs", ["7", "(1,240)", "(1,310)"]),
        ("Profit for the year", ["", "6,420", "5,330"]),
    ],
    columns=[330, 430, 520],
)

CORPUS = {
    "spanish_balance": SPANISH_BALANCE,
    "spanish_income_stacked": SPANISH_INCOME_STACKED,
    "english_oldest_first": ENGLISH_OLDEST_FIRST,
    "single_column": SINGLE_COLUMN,
    "fiscal_year": FISCAL_YEAR,
    "footnotes_thousands": FOOTNOTES_THOUSANDS,
}


def write_scanned(path: Path, statement: Statement, zoom: float = 2.0) -> Path:
    """The same statement as an image-only page, as a scanner produces it."""
    source = path.with_suffix(".src.pdf")
    write_statements(source, [statement])
    with fitz.open(source) as original:
        pixmap = original[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    scanned = fitz.open()
    page = scanned.new_page(width=595, height=842)
    page.insert_image(page.rect, pixmap=pixmap)
    scanned.save(path)
    scanned.close()
    source.unlink()
    return path
