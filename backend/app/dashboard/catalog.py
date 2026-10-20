"""What the dashboard can show (ID-HU-FE-004): entities, periods, statements.

Read deterministically from what ingestion already stored — no model calls:

- **Entity:** from the filename, without statement words, years, months and
  version suffixes ("EERR_Andina_2024.xlsx" -> "Andina"). Names that are the
  same company ("Andina" / "Andina S.A.S.") are grouped.
- **Periods:** from the column headings the chunker labels each value with
  ("2024=610000", "Three Months Ended June 30, 2026=…", "Al 31 de diciembre
  de 2025=…"). Full years and quarters only: year-to-date spans (six or nine
  months), budget and change columns are left out. A document without
  labelled columns falls back to the years in its filename and sheet titles.
- **Statements:** a document holds the income statement or the balance sheet
  when one of its rows is a standard line item of that statement (or its
  name / sheets say so). That is how the dashboard knows a solvency ratio for
  FY24 can't be calculated because the FY24 balance sheet isn't ingested.

Only each version group's default version, fully processed, counts; and only
documents the user's roles may see are listed.
"""
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.figures.extraction import (
    _CHANGE_COLUMN, _PLAN_COLUMN, _YEAR_IN_TEXT, _YEAR_TO_DATE, _column_end, coverage_of, entity_key,
    labelled_columns, same_entity, source_row_label,
)
from app.ratios.formulas import LINE_ITEMS
from app.ratios.mapping import normalize_label, statement_hint_score
from app.retrieval.access import can_access, parse_roles

STATEMENTS = ("income_statement", "balance_sheet")
STATEMENT_NAMES = {"income_statement": "income statement", "balance_sheet": "balance sheet"}

# Words in a filename that name the kind of document, not the company.
_NOT_ENTITY = {
    "eerr", "eeff", "esf", "pyg", "pnl", "p", "l", "bs", "is", "estado", "estados", "de", "del", "la", "el",
    "resultados", "resultado", "financieros", "financiero", "financiera", "situacion", "balance", "general",
    "sheet", "income", "statement", "statements", "financial", "financials", "position", "profit", "loss",
    "and", "y", "report", "reporte", "informe", "results", "resultados", "earnings", "release", "press",
    "quarterly", "annual", "anual", "trimestral", "fy", "q", "ytd", "consolidated", "consolidado",
    "consolidados", "individual", "separado", "separados", "final", "draft", "borrador", "version", "ver",
    "v", "copy", "copia", "sample", "ejemplo", "k", "form",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "setiembre",
    "octubre", "noviembre", "diciembre", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul",
    "aug", "sep", "oct", "nov", "dec",
}

# Each statement's line items, normalized as label_matches compares them.
_STATEMENT_LABELS = {
    statement: {
        normalize_label(label) for item in LINE_ITEMS.values() if item.statement == statement
        for label in item.accepted_labels
    } - {""}
    for statement in STATEMENTS
}

# Excel row chunks: "Sheet 'X' (context), <label>: <header>=<label>; ...".
_EXCEL_ROW_LABEL = re.compile(r", ([^:]+?): [^;=]*?=\1(?:;|$| \()")


def entity_from_filename(filename: str) -> str:
    """The company a document is about, as its filename names it."""
    stem = re.sub(r"\.(xlsx|xlsm|xls|pdf)$", "", filename, flags=re.IGNORECASE)
    stem = re.sub(r"[ _-]+v(?:er(?:sion)?)?\s*\d+(?:\.\d+)?$|\s*\(\d+\)$", "", stem, flags=re.IGNORECASE)
    tokens = re.split(r"[\s_\-.,]+", stem)
    kept = [
        t for t in tokens
        if t and not re.fullmatch(r"\d+|q[1-4]|[1-4]q|fy\d{2,4}|\d{1,2}[tq]", t, re.IGNORECASE)
        and t.lower() not in _NOT_ENTITY
    ]
    # Legal forms written with dots ("S.A.S") were split; glue single letters back.
    words: list[str] = []
    for token in kept:
        if len(token) == 1 and words and re.fullmatch(r"(?:[A-Za-z]\.?)+", words[-1]) and len(words[-1]) <= 4:
            words[-1] = f"{words[-1]}.{token}"
        else:
            words.append(token)
    name = " ".join(words).strip()
    if not name:
        return stem.strip() or filename
    return name if any(c.islower() for c in name) else name.title()


_NAMED_QUARTER = re.compile(r"\bq([1-4])\b|\b([1-4])q\b|\b([1-4])t\b", re.IGNORECASE)


@dataclass(frozen=True, order=True)
class Period:
    """A period the dashboard can calculate: a full year or a quarter."""

    sort: str        # "2024-5" for the year, "2026-2" for Q2 2026: sorts chronologically
    label: str       # "2024", "Q2 2026": what's shown and passed to the ratio engine


def year_period(year: int) -> Period:
    return Period(f"{year}-5", str(year))


def quarter_period(year: int, quarter: int) -> Period:
    return Period(f"{year}-{quarter}", f"Q{quarter} {year}")


def period_of_column(label: str) -> Period | None:
    """The full year or quarter a column heading stands for, or None for
    year-to-date spans, budget/plan and change columns, or no date at all."""
    if _CHANGE_COLUMN.search(label) or _PLAN_COLUMN.search(label) or _YEAR_TO_DATE.search(label):
        return None
    end = _column_end(label)
    if end is None:
        return None
    year, month, day = end
    named = _NAMED_QUARTER.search(label)
    if named:  # "Q1 2024", "1T 2024": the quarter is named, not dated
        return quarter_period(year, int(next(g for g in named.groups() if g)))
    coverage = coverage_of(label)
    if coverage in ("six_months", "nine_months"):
        return None
    if coverage == "three_months":
        return quarter_period(year, (month - 1) // 3 + 1)
    if coverage == "twelve_months" or (month, day) == (12, 31):
        return year_period(year)
    if coverage == "point_in_time":
        # A balance date: the quarter it closes.
        return quarter_period(year, (month - 1) // 3 + 1)
    return year_period(year)


def parse_period(text: str) -> Period | None:
    """A period typed by the user ("2024", "FY2024", "Q3 2026", "3Q26")."""
    value = text.strip()
    quarter = re.fullmatch(r"(?:q([1-4])|([1-4])q|([1-4])t)\s*(?:fy)?\s*'?(\d{2}|\d{4})", value, re.IGNORECASE)
    if quarter:
        q = int(quarter.group(1) or quarter.group(2) or quarter.group(3))
        year = int(quarter.group(4))
        return quarter_period(year + 2000 if year < 100 else year, q)
    year = re.fullmatch(r"(?:fy|año|ano)?\s*'?(\d{4})", value, re.IGNORECASE)
    if year:
        return year_period(int(year.group(1)))
    return period_of_column(value)


@dataclass
class CatalogDocument:
    id: str
    filename: str
    entity: str
    statements: list[str]                 # STATEMENTS it holds; empty = couldn't tell
    periods: list[Period] = field(default_factory=list)  # empty = couldn't tell
    confidentiality_tag: str = "restricted"

    def covers(self, period: Period) -> bool:
        return not self.periods or period in self.periods


@dataclass
class CatalogEntity:
    entity: str
    key: str
    documents: list[CatalogDocument]

    def visible_to(self, user_roles: str | None) -> "CatalogEntity | None":
        """The entity with only the documents `user_roles` may see (None if none)."""
        roles = parse_roles(user_roles)
        documents = [d for d in self.documents if can_access(d.confidentiality_tag, roles)]
        return CatalogEntity(self.entity, self.key, documents) if documents else None

    @property
    def periods(self) -> list[Period]:
        return sorted({p for d in self.documents for p in d.periods})

    def documents_for(self, period: Period) -> list[CatalogDocument]:
        return [d for d in self.documents if d.covers(period)]

    def statements_for(self, period: Period) -> set[str]:
        """Statements some document of the entity holds for `period`; a
        document whose statements couldn't be told counts as holding both,
        so nothing is reported missing on a guess."""
        held: set[str] = set()
        for document in self.documents_for(period):
            held |= set(document.statements or STATEMENTS)
        return held


def _row_label(chunk) -> str | None:
    if chunk.content_type == "table_row":
        return source_row_label(chunk.text)
    if chunk.sheet_name and chunk.cell_range:
        match = _EXCEL_ROW_LABEL.search(chunk.text)
        return match.group(1).strip() if match else None
    return None


def describe_document(document, chunks) -> CatalogDocument:
    statements: set[str] = set()
    # Periods of the statements' own line items (the ratio inputs) first: a
    # working column elsewhere in the workbook ("2016" in a cash-flow helper)
    # isn't a period the statements report.
    line_item_periods: set[Period] = set()
    other_periods: set[Period] = set()
    for chunk in chunks:
        label = _row_label(chunk)
        if label:
            keys = {normalize_label(label), normalize_label(label.rsplit(":", 1)[-1])} - {""}
            held = {s for s in STATEMENTS if keys & _STATEMENT_LABELS[s]}
            statements |= held
            for column, _ in labelled_columns(chunk.text):
                period = period_of_column(column)
                if period is not None:
                    (line_item_periods if held else other_periods).add(period)
    periods = line_item_periods or other_periods
    names = [document.filename, *document.sheet_names]
    statements |= {s for s in STATEMENTS if statement_hint_score(s, names) > 0}
    if not periods:
        # No dated columns: the years its filename or sheet titles give.
        context = " ".join([*names, *(c.text.split(",", 1)[0] for c in chunks[:5])])
        periods = {year_period(int(y)) for y in set(_YEAR_IN_TEXT.findall(context))}
    return CatalogDocument(
        id=str(document.id), filename=document.filename, entity=entity_from_filename(document.filename),
        statements=[s for s in STATEMENTS if s in statements], periods=sorted(periods),
        confidentiality_tag=document.confidentiality_tag,
    )


def build_catalog(db: Session, user_roles: str | None = None) -> list[CatalogEntity]:
    """Every entity with its current, processed documents (only those
    `user_roles` may see when given), alphabetically."""
    from app.db.models import Chunk, Document, DocumentStatus

    documents = db.scalars(
        select(Document).where(Document.is_current.is_(True), Document.status == DocumentStatus.ready.value)
        .order_by(Document.filename)
    ).all()
    if user_roles is not None:
        roles = parse_roles(user_roles)
        documents = [d for d in documents if can_access(d.confidentiality_tag, roles)]

    entities: list[CatalogEntity] = []
    for document in documents:
        chunks = db.scalars(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.page_number, Chunk.cell_range)
        ).all()
        described = describe_document(document, chunks)
        key = entity_key(described.entity) or described.entity.lower()
        group = next((e for e in entities if e.key == key or same_entity(e.entity, described.entity)), None)
        if group is None:
            entities.append(CatalogEntity(described.entity, key, [described]))
        else:
            group.documents.append(described)
            # The shortest name is the plainest ("Andina" over "Andina S.A.S.").
            if len(described.entity) < len(group.entity):
                group.entity = described.entity
    return sorted(entities, key=lambda e: e.entity.lower())


def find_entity(catalog: list[CatalogEntity], entity: str) -> CatalogEntity | None:
    key = entity_key(entity) or entity.lower()
    return next((e for e in catalog if e.key == key or same_entity(e.entity, entity)), None)
