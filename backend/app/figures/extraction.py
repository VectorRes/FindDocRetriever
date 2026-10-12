"""Extract one figure (e.g. "net income") from one document, with its citation.

Built for ID-HU-FE-003's cross-document comparison and shared with
ID-HU-BE-010's ratio engine (see docs/HANDOFF_BE-007_BE-010.md).

Per document: retrieve only within that document (ID-HU-BE-015's
`document_id` scope, so other documents can't crowd it out), ask the chat
model which source holds the figure and to copy the number exactly as
written, then **verify in code that the number appears in that source**
(ID-HU-BE-009's figure check). A figure that fails verification is reported
as not found; it is never invented.
"""
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus
from app.qa.confidence import match_figure, primary_reading
from app.qa.llm import get_chat_model
from app.qa.prompts import describe_location, label_chunks
from app.qa.schemas import ResolvedCitation
from app.ratios.mapping import normalize_label
from app.retrieval.access import can_access, parse_roles
from app.retrieval.service import RetrievedChunk, retrieve

logger = logging.getLogger(__name__)

MODEL_UNAVAILABLE = "The language model service didn't respond, so the figure couldn't be extracted. Try again."


class ExtractionServiceError(RuntimeError):
    """Every extraction in a request failed because the model (or embedding)
    service didn't respond: the caller should answer 503, not "not found"."""

UNIT_SCALES = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}
# Wording a document must actually contain before a scale is applied — the
# model's claim alone isn't trusted (it can turn 4,820,000 into 4.82 billion).
_SCALE_EVIDENCE = {
    "thousands": re.compile(r"\b(thousands?|miles|in 000s?|\(000\)|000s)\b", re.IGNORECASE),
    "millions": re.compile(r"\b(millions?|millones|mill[oó]n|mm|mill\.)\b", re.IGNORECASE),
    "billions": re.compile(r"\b(billions?|bn|miles de millones)\b", re.IGNORECASE),
}


def _documented_scale(scale: str, source_map: dict[str, RetrievedChunk]) -> bool:
    if scale == "units":
        return True
    evidence = _SCALE_EVIDENCE[scale]
    return any(evidence.search(chunk.text) for chunk in source_map.values())


class LLMFigure(BaseModel):
    """Structured-output contract for extracting one figure from SOURCES."""

    found: bool = Field(description="True if one of the SOURCES states the requested figure.")
    source_id: str | None = Field(
        default=None, description="The bracket id (e.g. 'S2') of the source the figure is taken from."
    )
    value_text: str | None = Field(
        default=None,
        description="The number exactly as written in that source, e.g. '4,820,000' or '(1.234,5)'.",
    )
    unit_scale: Literal["units", "thousands", "millions", "billions"] = Field(
        default="units",
        description=(
            "Only if the document text explicitly says its figures are in thousands/millions "
            "(e.g. 'cifras en miles', 'in thousands'); otherwise 'units'. Never guess from the size."
        ),
    )
    currency: str | None = Field(
        default=None,
        description="ISO currency code (COP, USD, EUR…) if the source, its headers or file name state it.",
    )
    period: str | None = Field(
        default=None,
        description=(
            "The period the figure is for, e.g. '2024', 'FY2024', 'Q3 2024'. Read it from the value's "
            "column header (e.g. '2024=610000' means 2024), the sheet title or the file name. "
            "Leave it empty only if nothing in the sources says which period it is."
        ),
    )
    label: str | None = Field(default=None, description="The line-item label exactly as written in the source.")
    entity: str | None = Field(
        default=None,
        description="The company the figure belongs to, as named in the source, its title or the file name.",
    )
    label_matches_metric: bool = Field(
        default=True,
        description=(
            "False if the label is a non-standard or only approximate name for the requested "
            "metric (e.g. 'Short-Term Obligations' for current liabilities)."
        ),
    )
    other_periods: list[str] = Field(
        default_factory=list,
        description="Other periods the same document gives this figure for, if any.",
    )
    column_header: str | None = Field(
        default=None,
        description="The column header the value sits under, exactly as written (e.g. 'Three Months Ended June 27, 2026').",
    )
    period_coverage: Literal[
        "three_months", "six_months", "nine_months", "twelve_months", "point_in_time", "unknown"
    ] = Field(
        default="unknown",
        description=(
            "How long a period the value covers: three/six/nine/twelve months for income-statement or "
            "cash-flow amounts; point_in_time for balance-sheet amounts at a date; unknown if not stated."
        ),
    )


EXTRACTION_PROMPT = """\
You extract ONE figure from a financial document for FinDoc Retriever. You \
are given the METRIC to find, an optional PERIOD, and numbered SOURCES taken \
from a single document.

- Find the source that states the METRIC's value. Use only the SOURCES; \
never compute, estimate or convert a figure yourself.
- Copy the number exactly as it is written in that source into value_text \
and give that source's id. Prefer a table row whose values are labelled \
with their column ("Three Months Ended June 30 2026=119,796") over page text \
where the numbers have no labels.
- If the document gives the metric for several periods: use PERIOD when it \
is given; otherwise use the most recent one (when there are both \
quarterly and year-to-date columns, the most recent quarter) and list the \
others in other_periods.
- Prefer the consolidated total for the whole entity over segment, \
geographic or product breakdowns of the same line item, and the primary \
financial statement's row over notes, highlights or narrative text. The \
first SOURCES are the document's table rows labelled with the METRIC's \
standard name, when it has any.
- Statements often have both quarterly and year-to-date columns (e.g. \
"Three Months Ended" and "Nine Months Ended"). Copy the header of the column \
the value is under into column_header and say what it covers in \
period_coverage. If PERIOD names a quarter, use the three-month column; if \
it names a year, use the twelve-month column.
- If no source states the metric, set found=false.
"""


@dataclass
class ExtractedFigure:
    document_id: str
    found: bool
    # The figure in base units (value as written x unit_scale).
    value: float | None = None
    value_text: str | None = None
    unit_scale: str = "units"
    currency: str | None = None
    period: str | None = None
    label: str | None = None
    # The company the figure is for, so callers combining documents (BE-010)
    # can tell two companies' statements apart.
    entity: str | None = None
    citation: ResolvedCitation | None = None
    confidence: str = "low"  # "high" | "medium" | "low"
    reason: str | None = None
    # What the value covers ("three_months", "nine_months", "point_in_time"...)
    # and the column it was read from, so callers can tell a quarter from
    # year-to-date figures that share a period label.
    period_coverage: str | None = None
    column_header: str | None = None
    # Why confidence isn't high, by kind: "scale", "label", "periods",
    # "coverage". `reason` joins them; callers with their own checks (e.g.
    # BE-010's label mapping) can drop the ones they settle.
    issues: dict[str, str] = field(default_factory=dict)
    # True when the model/embedding service failed (vs. "not in the document").
    error: bool = False


_COVERAGE_WORDS = {
    "three_months": "three months", "six_months": "six months", "nine_months": "nine months",
    "twelve_months": "twelve months", "point_in_time": "a single date",
}
_QUARTER = re.compile(r"\b(q[1-4]|[1-4]q|quarter|trimestre|[1-4]t)\b", re.IGNORECASE)
_HALF = re.compile(r"\b(h[12]|half|semestre|[12]s)\b", re.IGNORECASE)
_YEAR = re.compile(r"^\s*(fy\s*)?\d{4}\s*$|\b(fy\s*\d{2,4}|year|annual|año|anual)\b", re.IGNORECASE)


_PERIOD_WORDING = re.compile(
    r"\b(as of|as at|for the|(?:three|six|nine|twelve) months ended|quarters? ended|year to date|year-to-date"
    r"|years? ended|period ended|ended|at)\b",
    re.IGNORECASE,
)


def period_key(period: str | None) -> str | None:
    """A period normalized for comparison: wording and punctuation dropped, so
    "As of June 30, 2026" and "June 30, 2026", or "FY2024" and "2024", match.
    (Whether a value covers a quarter or the year to date is checked
    separately, through period_coverage.)"""
    if not period:
        return None
    text = _PERIOD_WORDING.sub(" ", period.lower())
    text = re.sub(r"\bfy\s*(\d{2,4})\b", r"\1", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split()) or None


# Legal-form suffixes ignored when telling companies apart ("Andina" = "Andina S.A.S.").
_LEGAL_FORM = re.compile(
    r"\b(s\.?\s?a\.?\s?s|s\.?\s?a|ltda|inc|ltd|llc|corp|corporation|company|co|plc|gmbh|s\.?\s?a\.?\s?de\s?c\.?\s?v)\b\.?",
    re.IGNORECASE,
)


def entity_key(value: str) -> str:
    """A company name normalized for comparison (case, punctuation, legal
    form, a web domain: "Amazon.com, Inc." -> "amazon")."""
    value = re.sub(r"\.(com|net|org|io)\b", " ", value.lower())
    return " ".join(re.sub(r"[^\w\s]", " ", _LEGAL_FORM.sub(" ", value)).split())


_NO_ENTITY = {"", "not specified", "unspecified", "unknown", "not stated", "not mentioned", "n a", "na", "none", "null"}


def known_entity(value: str | None) -> str | None:
    """The entity, or None when the model wrote a placeholder ("Not specified")."""
    return None if value is None or entity_key(value) in _NO_ENTITY else value


def same_entity(a: str, b: str) -> bool:
    """Whether two names are the same company: equal once normalized, or one
    is the other with more words ("Andina" / "Andina Holdings")."""
    words_a, words_b = set(entity_key(a).split()), set(entity_key(b).split())
    return bool(words_a and words_b) and (words_a <= words_b or words_b <= words_a)


_MONTHS = {
    "jan": 1, "ene": 1, "feb": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12, "dic": 12,
}
_QUARTER_ENDS = {(3, 31): 1, (6, 30): 2, (9, 30): 3, (12, 31): 4}


def period_aliases(period: str | None) -> set[str]:
    """Every name of a period: a calendar quarter-end date is also that
    quarter ("June 30, 2026" = "Q2 2026"), and December 31 is also the year."""
    key = period_key(period)
    if not key:
        return set()
    aliases = {key}
    numeric = re.search(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)", period or "")
    match = re.search(r"\b([a-z]{3})[a-z]* (\d{1,2}) (\d{4})\b", key)  # june 30 2026
    if numeric and (int(numeric.group(1)) > 12 or int(numeric.group(2)) > 12):
        # 31/12/2024 (day first) or 12/31/2024 (month first): the day is the part above 12.
        first, second = int(numeric.group(1)), int(numeric.group(2))
        day, month_number = (first, second) if first > 12 else (second, first)
        month = next((name for name, number in _MONTHS.items() if number == month_number), None)
        year = numeric.group(3)
    elif match:
        month, day, year = match.group(1), int(match.group(2)), match.group(3)
    else:
        # 30 de junio de 2026 / 31 December 2025
        match = re.search(r"\b(\d{1,2}) (?:de )?([a-z]{3})[a-z]* (?:del? )?(\d{4})\b", key)
        month, day, year = (match.group(2), int(match.group(1)), match.group(3)) if match else (None, 0, "")
    quarter = _QUARTER_ENDS.get((_MONTHS.get(month or ""), day))
    if quarter:
        aliases.add(f"q{quarter} {year}")
        if quarter == 4:
            aliases.add(year)
    return aliases


def same_period(a: str | None, b: str | None) -> bool:
    return bool(period_aliases(a) & period_aliases(b))


def distinct_periods(periods: list[str | None]) -> list[str]:
    """The genuinely different periods among `periods` (unknown ones left
    out): one representative of each group of names for the same period."""
    distinct: list[str] = []
    for period in periods:
        if period and not any(same_period(period, seen) for seen in distinct):
            distinct.append(period)
    return distinct


_NUMBER_TOKEN = re.compile(r"\d[\d.,]*\d|\d")
_LABELLED_VALUE = re.compile(r"(?:^|;\s*|:\s+)([^;=:]+?)=([^;]+)")


def column_of(value_text: str | None, source_text: str) -> str | None:
    """The column label a value sits under in a labelled source row
    ("... row 'Revenues': Quarter Ended June 30 2026=119,796; ..." ->
    "Quarter Ended June 30 2026"), or None when the row doesn't label it
    or several columns hold that same value."""
    if not value_text:
        return None
    number = _NUMBER_TOKEN.search(value_text)
    if number is None:
        return None
    target = primary_reading(number.group())
    labels = []
    for match in _LABELLED_VALUE.finditer(source_text):
        label, raw = match.group(1).strip(), match.group(2)
        found = _NUMBER_TOKEN.search(raw)
        # "column 8" is a position, not a heading: the row doesn't say which period it is.
        if found and label and not re.fullmatch(r"column \d+", label) and abs(primary_reading(found.group()) - target) < 1e-9:
            labels.append(label)
    distinct = list(dict.fromkeys(labels))
    return distinct[0] if len(distinct) == 1 else None


_ROW_LABEL = re.compile(r"\brow '([^']+)':")


def source_row_label(source_text: str) -> str | None:
    """The label of a PDF table row ("... row 'Cost of sales: Products': ...")."""
    match = _ROW_LABEL.search(source_text)
    return match.group(1).strip() if match and match.group(1) != "(unlabelled row)" else None


def coverage_of(label: str | None) -> str | None:
    """The span a column label covers, when it says so."""
    if not label:
        return None
    text = label.lower()
    if re.search(r"three months|quarters? ended|\bq[1-4]\b|trimestre|tres meses", text):
        return "three_months"
    if re.search(r"six months|semestre|seis meses|\bh[12]\b", text):
        return "six_months"
    if re.search(r"nine months|nueve meses", text):
        return "nine_months"
    if re.search(r"twelve months|years? ended|fiscal year|full year|\bannual\b|doce meses|a[ñn]os? terminados?|\banual\b", text):
        return "twelve_months"
    stripped = text.strip()
    if (
        re.search(r"\bas (?:of|at)\b|^al?\s+\d", text)
        or re.fullmatch(r"[a-z]+\.?\s+\d{1,2},\s*\d{4}", stripped)          # june 27, 2026
        or re.fullmatch(r"\d{1,2}\s+[a-z]+\.?\s+\d{4}", stripped)           # 31 december 2025
        or re.fullmatch(r"\d{1,2}\s+de\s+[a-záéíóú]+\s+del?\s+\d{4}", stripped)  # 31 de diciembre de 2025
    ):
        return "point_in_time"  # a balance date, not a span
    return None


def expected_coverage(period: str | None) -> set[str] | None:
    """Coverages consistent with a requested period, or None if it doesn't say."""
    if not period:
        return None
    if _QUARTER.search(period):
        return {"three_months", "point_in_time"}
    if _HALF.search(period):
        return {"six_months", "point_in_time"}
    if _YEAR.search(period):
        return {"twelve_months", "point_in_time"}
    return None


def _citation(source_id: str, chunk: RetrievedChunk) -> ResolvedCitation:
    return ResolvedCitation(
        source_id=source_id,
        document_id=chunk.document_id,
        document_filename=chunk.document_filename,
        sheet_name=chunk.sheet_name,
        cell_range=chunk.cell_range,
        page_number=chunk.page_number,
        reference_number=chunk.reference_number,
        text=chunk.text,
    )


def _build_prompt(metric: str, period: str | None, source_map: dict[str, RetrievedChunk]) -> str:
    lines = [f"METRIC: {metric}", f"PERIOD: {period or '(not specified)'}", "", "SOURCES:"]
    for source_id, chunk in source_map.items():
        lines.append(f"[{source_id}] ({describe_location(chunk)})")
        lines.append(chunk.text.strip())
        lines.append("")
    return "\n".join(lines)


_YEAR_IN_TEXT = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def single_year(text: str, value_text: str | None = None) -> str | None:
    """The year a source mentions, when it mentions exactly one ("Año 2024 …
    2024=610000" -> "2024"). Fallback for when the model leaves the period
    empty; a source naming several years is ambiguous, so it gives None.
    The figure itself is left out, so a value of 2010 isn't read as a year."""
    if value_text:
        digits = re.sub(r"\D", "", value_text)
        text = re.sub(rf"(?<!\d){digits}(?!\d)", " ", text) if digits else text
    years = set(_YEAR_IN_TEXT.findall(text))
    return years.pop() if len(years) == 1 else None


_CURRENCY_MENTIONS = {
    "COP": re.compile(r"\bCOP\b|pesos colombianos", re.IGNORECASE),
    "USD": re.compile(r"\bUSD\b|US\$|d[oó]lares|\bdollars?\b", re.IGNORECASE),
    "EUR": re.compile(r"\bEUR\b|€|\beuros?\b", re.IGNORECASE),
    "MXN": re.compile(r"\bMXN\b|pesos mexicanos", re.IGNORECASE),
    "PEN": re.compile(r"\bPEN\b|soles", re.IGNORECASE),
    "CLP": re.compile(r"\bCLP\b|pesos chilenos", re.IGNORECASE),
    "GBP": re.compile(r"\bGBP\b|£|pounds? sterling", re.IGNORECASE),
}


def single_currency(text: str) -> str | None:
    """The currency a source states, when it states exactly one ("Cifras en
    pesos colombianos (COP)" -> "COP"). Fallback for when the model leaves
    the currency empty; a source naming several is ambiguous, so it gives None."""
    found = [code for code, pattern in _CURRENCY_MENTIONS.items() if pattern.search(text)]
    return found[0] if len(found) == 1 else None


def _verified(
    source_map: dict[str, RetrievedChunk], source_id: str | None, value_text: str | None
) -> tuple[float, ResolvedCitation] | None:
    """The number and citation, only if `value_text` really appears in that source."""
    chunk = source_map.get(source_id or "")
    if chunk is None or not value_text:
        return None
    value = match_figure(value_text, chunk.text)
    if value is None:
        return None
    if value_text.strip().startswith("(") and value > 0:
        value = -value  # accounting negative: (1,234)
    return value, _citation(source_id, chunk)


MAX_ROW_LOOKUP = 5


def _labelled_rows_with(
    db: Session, document: Document, value_text: str | None, user_roles: str | None, user_id: str | None,
    question: str,
) -> dict[str, RetrievedChunk]:
    """PDF table rows of `document` that contain `value_text`, as extra
    sources ("R1", "R2"…), access-checked and audited like retrieval."""
    from app.db.models import Chunk, RetrievalAuditLog

    if not value_text or not value_text.strip():
        return {}
    number = _NUMBER_TOKEN.search(value_text)
    if number is None:
        return {}
    found = db.scalars(
        select(Chunk).where(
            Chunk.document_id == document.id,
            Chunk.content_type == "table_row",
            Chunk.text.contains(number.group()),
        ).limit(MAX_ROW_LOOKUP * 4)
    ).all()
    return _as_sources(db, document, found, user_roles, user_id, question, "R", MAX_ROW_LOOKUP)


def _as_sources(
    db: Session, document: Document, chunks, user_roles: str | None, user_id: str | None, question: str,
    prefix: str, limit: int,
) -> dict[str, RetrievedChunk]:
    """Chunks looked up directly in the document, as sources ("R1"…),
    access-checked and audited like retrieval."""
    from app.db.models import RetrievalAuditLog

    roles = parse_roles(user_roles)
    rows: dict[str, RetrievedChunk] = {}
    for chunk in chunks:
        if len(rows) >= limit or not can_access(chunk.confidentiality_tag, roles):
            continue
        rows[f"{prefix}{len(rows) + 1}"] = RetrievedChunk(
            chunk_id=str(chunk.id), document_id=str(chunk.document_id), document_filename=document.filename,
            sheet_name=chunk.sheet_name, cell_range=chunk.cell_range, page_number=chunk.page_number,
            text=chunk.text, content_type=chunk.content_type, extraction_method=chunk.extraction_method,
            confidence=chunk.confidence, confidence_label=chunk.confidence_label,
            confidentiality_tag=chunk.confidentiality_tag,
        )
        db.add(RetrievalAuditLog(
            user_id=user_id or "anonymous", roles=sorted(roles), action="retrieve",
            document_id=chunk.document_id, chunk_id=chunk.id,
            confidentiality_tag=chunk.confidentiality_tag, authorized=True, question=question,
        ))
    if rows:
        db.flush()
    return rows


MAX_LABELLED_ROWS = 6


def _row_label_keys(label: str) -> set[str]:
    """A row label's comparable forms: whole, and without its section
    ("Current assets: Inventories" -> also "inventories")."""
    return {normalize_label(label), normalize_label(label.rsplit(":", 1)[-1])} - {""}


def rows_labelled(
    db: Session, document: Document, labels: Collection[str], exclude: Collection[str],
    user_roles: str | None, user_id: str | None, question: str,
) -> tuple[dict[str, RetrievedChunk], bool]:
    """Table rows of `document` labelled with one of `labels` (the metric's
    standard names), so the statement line itself is a source even when
    similarity search ranks a note, a segment table or narrative text above
    it. Exact label matches only, when there are any; otherwise rows whose
    label starts with a standard name ("Accounts receivable, net and
    other"). Rows labelled with an `exclude` name (another line item, e.g.
    "Total liabilities and equity" for total liabilities) never count.
    Also says whether the rows are exact matches."""
    from app.db.models import Chunk

    wanted = {normalize_label(label) for label in labels} - {""}
    unwanted = {normalize_label(label) for label in exclude} - {""}
    if not wanted:
        return {}, False
    exact, prefixed = [], []
    for chunk in db.scalars(
        select(Chunk).where(Chunk.document_id == document.id, Chunk.content_type == "table_row")
        .order_by(Chunk.page_number)
    ):
        row_label = source_row_label(chunk.text)
        if not row_label:
            continue
        keys = _row_label_keys(row_label)
        if keys & (unwanted - wanted) or not _has_period_columns(chunk.text):
            # A row whose columns aren't labelled with periods ("column 3=…",
            # or a mangled heading) can't be read reliably: it isn't put first.
            continue
        if keys & wanted:
            exact.append(chunk)
        elif any(key.startswith(f"{name} ") for key in keys for name in wanted):
            prefixed.append(chunk)
    # A total before its components ("Total efectivo y equivalentes" before "Caja y bancos").
    rows = sorted(exact or prefixed, key=lambda c: not (source_row_label(c.text) or "").lower().startswith("total"))
    sources = _as_sources(db, document, rows, user_roles, user_id, question, "L", MAX_LABELLED_ROWS)
    return sources, bool(exact)


def _has_period_columns(source_text: str) -> bool:
    """Every labelled value of the row sits under a heading naming a period
    (a year or a date), and at least one does."""
    columns = labelled_columns(source_text)
    with_year = [label for label, _ in columns if _YEAR_IN_TEXT.search(label)]
    return bool(with_year) and "column " not in source_text.split("row '", 1)[-1] and all(
        _YEAR_IN_TEXT.search(label) or _CHANGE_COLUMN.search(label) for label, _ in columns
    ) and all(_reads_as_period(label) for label in with_year)


# Change columns of comparative statements ("Variación absoluta", "% Change").
_CHANGE_COLUMN = re.compile(r"variaci[oó]n|variation|change|diferencia|\bvar\b", re.IGNORECASE)
# Words a period heading is made of, in English and Spanish.
_PERIOD_WORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "setiembre",
    "octubre", "noviembre", "diciembre", "ene", "abr", "ago", "dic",
    "three", "six", "nine", "twelve", "months", "month", "ended", "ending", "quarter", "quarters", "year",
    "years", "fiscal", "to", "date", "ytd", "as", "of", "at", "period", "full", "first", "second", "third",
    "fourth", "half", "tres", "seis", "nueve", "doce", "meses", "terminados", "terminado", "terminadas",
    "trimestre", "semestre", "año", "años", "ano", "acumulado", "al", "a", "de", "del", "el", "la", "y", "and",
    "fy", "q", "real", "actual", "budget", "presupuesto", "restated", "reexpresado", "unaudited", "audited",
}


def _reads_as_period(label: str) -> bool:
    """A column heading made only of a period's words and numbers ("Three
    Months Ended June 27, 2026", "31/12/2024", "Año 2024"), not one the
    table grid cut mid-word ("years ended mber")."""
    words = re.findall(r"[a-záéíóúñ]+", label.lower())
    return all(word in _PERIOD_WORDS or re.fullmatch(r"q[1-4]|[1-4]?q", word) for word in words)


def labelled_columns(source_text: str) -> list[tuple[str, str]]:
    """The (column label, value as written) pairs of a labelled source row,
    in order; "column N" positions aren't labels and are left out."""
    columns = []
    for match in _LABELLED_VALUE.finditer(source_text):
        label, raw = match.group(1).strip(), match.group(2).strip()
        if label and _NUMBER_TOKEN.search(raw) and not re.fullmatch(r"column \d+", label):
            columns.append((label, raw))
    return columns


_YEAR_TO_DATE = re.compile(r"year to date|year-to-date|\bytd\b|acumulado", re.IGNORECASE)
_PLAN_COLUMN = re.compile(r"presupuest|budget|forecast|proyecci|projected|plan\b|estimad|target|objetivo", re.IGNORECASE)
_MONTH_DAY = re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:de\s+)?([A-Za-z]{3})[a-z]*")
_SPAN_RANK = {"point_in_time": 0, "three_months": 1, "six_months": 2, "nine_months": 3, "twelve_months": 4}


def _column_end(label: str) -> tuple[int, int, int] | None:
    """When a column ends (year, month, day); a bare year counts as its end."""
    years = _YEAR_IN_TEXT.findall(label)
    if not years:
        return None
    numeric = re.search(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)", label)
    if numeric:
        first, second = int(numeric.group(1)), int(numeric.group(2))
        day, month = (second, first) if second > 12 else (first, second)  # day first unless impossible
        return int(numeric.group(3)), month, day
    match = _MONTH_DAY.search(label)
    if match:
        month = _MONTHS.get((match.group(1) or match.group(4) or "").lower()[:3])
        day = int(match.group(2) or match.group(3))
        if month:
            return int(years[-1]), month, day
    return int(years[-1]), 12, 31


def _span_rank(label: str) -> float:
    """Shorter spans first (the quarter before year-to-date); unknown last."""
    if _YEAR_TO_DATE.search(label):
        return 5
    return _SPAN_RANK.get(coverage_of(label) or "", 4.5)


def _column_fits(label: str, period: str) -> bool:
    if same_period(label, period):
        return True
    years = _YEAR_IN_TEXT.findall(period)
    return bool(years) and _YEAR_IN_TEXT.findall(label)[-1:] == years[-1:]


def statement_row(
    source_map: dict[str, RetrievedChunk], row_ids: set[str], period: str | None
) -> tuple[tuple[str, RetrievedChunk], tuple[str, str]] | None:
    """The metric's statement row (one of `row_ids`) and the column to read,
    when every such row gives the same value for the period; else None."""
    picks = []
    for source_id, chunk in source_map.items():
        if chunk.chunk_id in row_ids:
            column = choose_column(labelled_columns(chunk.text), period)
            if column is None:
                return None
            picks.append(((source_id, chunk), column))
    values = {primary_reading(_NUMBER_TOKEN.search(raw).group()) for _, (_, raw) in picks}
    return picks[0] if len(values) == 1 else None


def stated_scale(text: str) -> str | None:
    """The scale a source states for its own figures ("(in millions, except
    per share data)" -> "millions"), when it states exactly one."""
    found = [scale for scale, pattern in _SCALE_EVIDENCE.items() if pattern.search(text)]
    return found[0] if len(found) == 1 else None


def choose_column(columns: list[tuple[str, str]], period: str | None) -> tuple[str, str] | None:
    """The column a figure should be read from, decided from the row's own
    column labels instead of the model's reading: with `period`, the column
    for that period (and the span it implies: a quarter's three-month
    column, not year-to-date); without one, the most recent actual column
    (not a budget), the quarter before year-to-date. None when the labels
    don't settle it, so the model's choice stands."""
    if len(columns) < 2:
        return None
    if period:
        expected = expected_coverage(period)
        candidates = [
            (label, raw) for label, raw in columns
            if _column_fits(label, period) and (
                not expected
                or coverage_of(label) in expected
                or (coverage_of(label) is None and not _YEAR_TO_DATE.search(label))
            )
        ]
    else:
        actual = [(label, raw) for label, raw in columns if not _PLAN_COLUMN.search(label)] or columns
        ends = {label: _column_end(label) for label, _ in actual}
        latest = max((end for end in ends.values() if end), default=None)
        candidates = [(label, raw) for label, raw in actual if latest and ends[label] == latest]
    if not candidates:
        return None
    best = min(_span_rank(label) for label, _ in candidates)
    chosen = [(label, raw) for label, raw in candidates if _span_rank(label) == best]
    values = {primary_reading(_NUMBER_TOKEN.search(raw).group()) for _, raw in chosen}
    return chosen[0] if len(values) == 1 else None


def labelled_source(
    source_map: dict[str, RetrievedChunk], source_id: str | None, value_text: str | None, label: str | None
) -> tuple[str, RetrievedChunk] | None:
    """A table row holding the same figure under a column label, when the
    model cited page text without labels: citing the row lets the period
    column be confirmed instead of guessed. When several rows hold the
    figure, the one whose row label contains the model's label is used;
    ambiguous otherwise, so None."""
    chosen = source_map.get(source_id or "")
    if chosen is None or not value_text or column_of(value_text, chosen.text):
        return None
    candidates = [
        (sid, chunk) for sid, chunk in source_map.items()
        if sid != source_id and column_of(value_text, chunk.text)
    ]
    if len(candidates) > 1 and label:
        wanted = " ".join(re.findall(r"[a-z]+", label.lower()))
        matching = [
            (sid, chunk) for sid, chunk in candidates
            if wanted and wanted in " ".join(re.findall(r"[a-z]+", (source_row_label(chunk.text) or "").lower()))
        ]
        candidates = matching or candidates
    # Several rows are fine when they all put the figure under the same column
    # (net income repeated in the income, comprehensive income and equity statements).
    if candidates and len({column_of(value_text, chunk.text) for _, chunk in candidates}) == 1:
        return candidates[0]
    return None


def extract_figure(
    db: Session,
    document_id: UUID | str,
    metric: str,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    period: str | None = None,
    labels: Collection[str] = (),
    exclude_labels: Collection[str] = (),
) -> ExtractedFigure:
    """The value of `metric` in one document, with its citation (see module docstring).

    `labels` are the metric's standard line-item names: table rows with one
    of them are sources even if similarity search misses them, and rows
    labelled with an `exclude_labels` name are never offered for it."""
    document = db.get(Document, UUID(str(document_id)))
    result = ExtractedFigure(document_id=str(document_id), found=False)
    if document is None:
        result.reason = "Document not found."
        return result
    if document.status != DocumentStatus.ready.value:
        result.reason = "This document hasn't finished processing."
        return result
    if not can_access(document.confidentiality_tag, parse_roles(user_roles)):
        result.reason = "This document is restricted for your role."
        return result

    query = f"{metric} {period}" if period else metric
    try:
        # No relevance floor: the document is already chosen, and the model
        # decides below whether the metric is there (a short metric name in
        # another language than the statement can score low).
        chunks = retrieve(
            db, question=query, top_k=8, user_roles=user_roles, user_id=user_id, document_id=document.id,
            min_score=0,
        )
        # The statement's own rows for the metric go first.
        found_rows, exact_rows = rows_labelled(db, document, labels, exclude_labels, user_roles, user_id, query)
        rows = list(found_rows.values())
        listed = {row.chunk_id for row in rows}
        chunks = rows + [chunk for chunk in chunks if chunk.chunk_id not in listed]
        if not chunks:
            result.reason = "No accessible content in this document matches the metric."
            return result

        source_map = label_chunks(chunks)
        figure = get_chat_model().with_structured_output(LLMFigure).invoke(
            [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": _build_prompt(metric, period, source_map)},
            ]
        )
    except Exception:  # provider/network errors surface under many exception types
        logger.exception("Figure extraction failed for %s in document %s", metric, document_id)
        result.reason = MODEL_UNAVAILABLE
        result.error = True
        return result

    if not figure.found:
        result.reason = f"This document doesn't state {metric}."
        return result

    verified = _verified(source_map, figure.source_id, figure.value_text)
    if verified is None:
        result.reason = "The extracted figure couldn't be found in its source, so it isn't shown."
        return result

    value, citation = verified
    labelled = labelled_source(source_map, figure.source_id, figure.value_text, figure.label)
    if labelled is None and not column_of(figure.value_text, source_map[figure.source_id].text):
        # Retrieval's top-k may have left the labelled row out: look it up in the document.
        rows = _labelled_rows_with(db, document, figure.value_text, user_roles, user_id, query)
        labelled = labelled_source({figure.source_id: source_map[figure.source_id], **rows},
                                   figure.source_id, figure.value_text, figure.label)
    if labelled is not None:
        citation = _citation(*labelled)
    elif exact_rows and not column_of(figure.value_text, citation.text):
        # The model quoted text ("$2.3 billion" in the discussion) although the
        # statement's own row for the metric is a source: use the row, when
        # its column labels settle which value it is and every such row agrees.
        row = statement_row(source_map, listed, period)
        if row is not None:
            (source_id, chunk), (_, raw) = row
            reread = match_figure(raw, chunk.text)
            if reread is not None:
                value, citation = (-reread if raw.startswith("(") and reread > 0 else reread), _citation(source_id, chunk)
    # Which column: decided from the row's own column labels when they settle
    # it. The model picks the wrong one too often to be trusted with it (the
    # first column when tables run oldest to newest, the nine-month column
    # for a quarter, the prior year's column), and nothing else would notice.
    value_text, model_period = figure.value_text, figure.period
    column = choose_column(labelled_columns(citation.text), period)
    if column is not None and column_of(value_text, citation.text) != column[0]:
        reread = match_figure(column[1], citation.text)
        if reread is not None:
            value = -reread if column[1].startswith("(") and reread > 0 else reread
            value_text, model_period = column[1], column[0]
    issues: dict[str, str] = {}
    # The cited source's own statement of scale wins over the model's reading.
    unit_scale = stated_scale(citation.text) or figure.unit_scale
    if not _documented_scale(unit_scale, source_map):
        unit_scale = "units"
        issues["scale"] = (
            f"The figures may be in {figure.unit_scale}, but the document doesn't say so; "
            "the value is shown as written."
        )
    if not figure.label_matches_metric:
        issues["label"] = f"The line item is labelled '{figure.label}', which may not be exactly {metric}."
    if figure.other_periods and not period:
        issues["periods"] = (
            f"The document also has {', '.join(figure.other_periods)}; the most recent period was used."
        )
    # The column as the source row labels it ("Nine Months Ended June 27,
    # 2026=364,357") beats the model's reading, which often leaves it out.
    source_column = column_of(value_text, citation.text)
    column_header = source_column or figure.column_header
    coverage = coverage_of(source_column) or (
        None if figure.period_coverage == "unknown" else figure.period_coverage
    )
    expected = expected_coverage(period)
    if expected and coverage and coverage not in expected:
        header = f" ({column_header})" if column_header else ""
        issues["coverage"] = (
            f"The value covers {_COVERAGE_WORDS[coverage]}{header}, "
            f"which doesn't match the period requested ({period})."
        )

    return ExtractedFigure(
        document_id=str(document.id),
        found=True,
        value=value * UNIT_SCALES[unit_scale],
        value_text=value_text,
        unit_scale=unit_scale,
        currency=(figure.currency or "").upper() or single_currency(citation.text),
        period=model_period or single_year(citation.text, value_text),
        # The row's own label when the source is a table row: the model may name
        # "Cost of sales: Products" as plain "Cost of sales".
        label=(source_row_label(citation.text) if source_column else None) or figure.label,
        entity=known_entity(figure.entity),
        citation=citation,
        confidence="medium" if issues else "high",
        reason=" ".join(issues.values()) or None,
        period_coverage=coverage,
        column_header=column_header,
        issues=issues,
    )


# --- documented exchange rates (FE-003 alternate case: multi-currency) -----

class LLMExchangeRate(BaseModel):
    found: bool = Field(description="True if one of the SOURCES states an exchange rate between the two currencies.")
    source_id: str | None = Field(default=None, description="The bracket id of the source stating the rate.")
    rate_text: str | None = Field(default=None, description="The rate exactly as written in that source.")
    base_currency: str | None = Field(
        default=None, description="The currency of which ONE unit equals rate_text units of quote_currency."
    )
    quote_currency: str | None = Field(default=None, description="The currency the rate is expressed in.")


EXCHANGE_RATE_PROMPT = """\
You look for a DOCUMENTED exchange rate between two currencies in the \
SOURCES of a financial document collection. Use only what a source \
states; never use a market rate you know. Copy the rate exactly as written, \
and say which currency is the base (1 unit of base = rate units of quote).
"""


@dataclass
class ExchangeRate:
    from_currency: str
    to_currency: str
    rate: float  # 1 from_currency = rate to_currency
    rate_text: str
    citation: ResolvedCitation


def orient_rate(rate: float, base: str, quote: str, from_currency: str, to_currency: str) -> float | None:
    """Turn a documented base/quote rate into the from->to multiplier, or None."""
    base, quote = base.upper(), quote.upper()
    if (base, quote) == (from_currency, to_currency):
        return rate
    if (base, quote) == (to_currency, from_currency) and rate:
        return 1 / rate
    return None


def find_exchange_rate(
    db: Session,
    from_currency: str,
    to_currency: str,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
) -> ExchangeRate | None:
    """A documented `from_currency` -> `to_currency` rate from the accessible
    corpus (current versions), with its citation, or None if none is documented."""
    chunks = retrieve(
        db,
        question=f"exchange rate {from_currency} {to_currency} tasa de cambio TRM",
        top_k=8,
        user_roles=user_roles,
        user_id=user_id,
    )
    if not chunks:
        return None
    source_map = label_chunks(chunks)
    lines = [f"CURRENCIES: {from_currency} and {to_currency}", "", "SOURCES:"]
    for source_id, chunk in source_map.items():
        lines += [f"[{source_id}] ({describe_location(chunk)})", chunk.text.strip(), ""]
    try:
        found = get_chat_model().with_structured_output(LLMExchangeRate).invoke(
            [
                {"role": "system", "content": EXCHANGE_RATE_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ]
        )
    except Exception:
        logger.exception("Exchange-rate lookup failed for %s -> %s", from_currency, to_currency)
        return None  # treated like an undocumented rate: values are shown unconverted
    if not found.found or not found.base_currency or not found.quote_currency:
        return None
    verified = _verified(source_map, found.source_id, found.rate_text)
    if verified is None:
        return None
    rate, citation = verified
    oriented = orient_rate(abs(rate), found.base_currency, found.quote_currency, from_currency, to_currency)
    if oriented is None:
        return None
    return ExchangeRate(from_currency, to_currency, oriented, found.rate_text, citation)
