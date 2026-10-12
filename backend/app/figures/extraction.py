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
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus
from app.qa.confidence import match_figure
from app.qa.llm import get_chat_model
from app.qa.prompts import describe_location, label_chunks
from app.qa.schemas import ResolvedCitation
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
and give that source's id.
- If the document gives the metric for several periods: use PERIOD when it \
is given; otherwise use the most recent one and list the others in \
other_periods.
- Prefer the consolidated total for the whole entity over segment, \
geographic or product breakdowns of the same line item.
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
    """A company name normalized for comparison (case, punctuation, legal form)."""
    return " ".join(re.sub(r"[^\w\s]", " ", _LEGAL_FORM.sub(" ", value.lower())).split())


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
    match = re.search(r"\b([a-z]{3})[a-z]* (\d{1,2}) (\d{4})\b", key)  # june 30 2026
    if match:
        month, day, year = match.group(1), int(match.group(2)), match.group(3)
    else:
        match = re.search(r"\b(\d{1,2}) de ([a-z]{3})[a-z]* de (\d{4})\b", key)  # 30 de junio de 2026
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


def extract_figure(
    db: Session,
    document_id: UUID | str,
    metric: str,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    period: str | None = None,
) -> ExtractedFigure:
    """The value of `metric` in one document, with its citation (see module docstring)."""
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
        chunks = retrieve(
            db, question=query, top_k=8, user_roles=user_roles, user_id=user_id, document_id=document.id
        )
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
    issues: dict[str, str] = {}
    unit_scale = figure.unit_scale
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
    expected = expected_coverage(period)
    if expected and figure.period_coverage not in expected | {"unknown"}:
        header = f" ({figure.column_header})" if figure.column_header else ""
        issues["coverage"] = (
            f"The value covers {_COVERAGE_WORDS[figure.period_coverage]}{header}, "
            f"which doesn't match the period requested ({period})."
        )

    return ExtractedFigure(
        document_id=str(document.id),
        found=True,
        value=value * UNIT_SCALES[unit_scale],
        value_text=figure.value_text,
        unit_scale=unit_scale,
        currency=(figure.currency or "").upper() or single_currency(citation.text),
        period=figure.period or single_year(citation.text, figure.value_text),
        label=figure.label,
        entity=figure.entity,
        citation=citation,
        confidence="medium" if issues else "high",
        reason=" ".join(issues.values()) or None,
        period_coverage=None if figure.period_coverage == "unknown" else figure.period_coverage,
        column_header=figure.column_header,
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
