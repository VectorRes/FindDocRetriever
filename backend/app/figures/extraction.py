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
import re
from dataclasses import dataclass
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
    period: str | None = Field(default=None, description="The period the figure is for, e.g. 'FY2024', 'Q3 2024'.")
    label: str | None = Field(default=None, description="The line-item label exactly as written in the source.")
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
    citation: ResolvedCitation | None = None
    confidence: str = "low"  # "high" | "medium" | "low"
    reason: str | None = None


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
    if not figure.found:
        result.reason = f"This document doesn't state {metric}."
        return result

    verified = _verified(source_map, figure.source_id, figure.value_text)
    if verified is None:
        result.reason = "The extracted figure couldn't be found in its source, so it isn't shown."
        return result

    value, citation = verified
    reasons = []
    confidence = "high"
    unit_scale = figure.unit_scale
    if not _documented_scale(unit_scale, source_map):
        unit_scale = "units"
        confidence = "medium"
        reasons.append(
            f"The figures may be in {figure.unit_scale}, but the document doesn't say so; "
            "the value is shown as written."
        )
    if not figure.label_matches_metric:
        confidence = "medium"
        reasons.append(f"The line item is labelled '{figure.label}', which may not be exactly {metric}.")
    if figure.other_periods and not period:
        confidence = "medium"
        reasons.append(
            f"The document also has {', '.join(figure.other_periods)}; the most recent period was used."
        )

    return ExtractedFigure(
        document_id=str(document.id),
        found=True,
        value=value * UNIT_SCALES[unit_scale],
        value_text=figure.value_text,
        unit_scale=unit_scale,
        currency=(figure.currency or "").upper() or None,
        period=figure.period,
        label=figure.label,
        citation=citation,
        confidence=confidence,
        reason=" ".join(reasons) or None,
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
    found = get_chat_model().with_structured_output(LLMExchangeRate).invoke(
        [
            {"role": "system", "content": EXCHANGE_RATE_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ]
    )
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
