"""Ratio engine (ID-HU-BE-010): from selected documents to cited ratios.

1. `map_line_items` finds each required input once, across all the selected
   documents: it tries the document whose name suggests the right statement
   first (income statement vs balance sheet) and keeps the best-confidence
   hit, so a ratio like ROA combines net income from the income statement
   with total assets from the balance sheet automatically.
2. `compute_ratio` does the arithmetic deterministically (no model) and
   attaches every input's citation, flagging what the analyst should check:
     - an input whose label isn't a standard name for it  -> needs_review;
     - inputs from different periods                        -> needs_review;
     - inputs in different currencies, a missing input, or
       a zero denominator                                   -> not_calculable
       (ID-HU-BE-011: never a blank or a misleading value; a
       missing input is named in `missing_inputs`).

The model is only involved inside `extract_figure` (one call per input and
document tried), whose values are verified to appear in their source.
"""
import contextvars
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.observability import span
from app.figures.extraction import (
    UNIT_SCALES, ExtractedFigure, ExtractionServiceError, column_of, distinct_periods, entity_key, extract_figure,
    same_entity,
)
from app.qa.schemas import ResolvedCitation
from app.ratios.formulas import CATEGORIES, LINE_ITEMS, POSITIVE_ITEMS, RATIOS, LineItem, RatioDefinition
from app.ratios.mapping import label_matches, names_other_item, other_labels, statement_hint_score

Confidence = Literal["high", "medium", "low"]
_RANK = {"high": 2, "medium": 1, "low": 0}

Extractor = Callable[..., ExtractedFigure]


@dataclass
class MappedInput:
    """One formula input as found in a document (or not found)."""

    key: str
    name: str
    statement: str
    found: bool
    value: float | None = None
    value_text: str | None = None
    unit_scale: str = "units"
    currency: str | None = None
    period: str | None = None
    label: str | None = None
    # The company the figure is for (from extraction), if known.
    entity: str | None = None
    # "three_months", "nine_months", "point_in_time"... and the column read.
    period_coverage: str | None = None
    column_header: str | None = None
    document_id: str | None = None
    document_filename: str | None = None
    citation: ResolvedCitation | None = None
    # Combines extraction confidence with whether the label is a standard name.
    confidence: Confidence = "low"
    # True when the label isn't an accepted name for this input: the analyst
    # should confirm the mapping before trusting ratios that use it.
    needs_review: bool = False
    reason: str | None = None
    # The value covers a different length of time than the requested period
    # (FE-003's "coverage" issue): ratios using it are held for review.
    coverage_mismatch: bool = False
    # Not found because the model service failed, not because it's missing.
    error: bool = False
    # Not a line in the statement but computed from these inputs (e.g. total
    # liabilities = total assets - total equity); their citations back it.
    derived_from: list["MappedInput"] = field(default_factory=list)


@dataclass
class RatioResult:
    key: str
    name: str
    category: str
    formula: str
    unit: str
    description: str
    # "calculated" | "needs_review" (value shown only after the analyst reviews
    # the flagged inputs) | "not_calculable" (value is None, see reasons).
    status: str
    value: float | None
    inputs: list[MappedInput]
    # Every line item used, deduplicated, in input order.
    citations: list[ResolvedCitation]
    # The inputs came from more than one document.
    cross_document: bool
    currency: str | None = None
    period: str | None = None
    reasons: list[str] = field(default_factory=list)
    # Names of the inputs that weren't found ("Current liabilities"), when
    # that's why the ratio is not calculable (ID-HU-BE-011).
    missing_inputs: list[str] = field(default_factory=list)


@dataclass
class RatioReport:
    period: str | None
    document_ids: list[str]
    ratios: list[RatioResult]
    line_items: list[MappedInput]
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DocumentRef:
    """What the engine needs from a Document, read once on the request's
    session so worker threads never touch that session's ORM objects."""

    id: UUID
    filename: str
    sheet_names: tuple[str, ...] = ()
    is_current: bool = True

    @classmethod
    def of(cls, document) -> "DocumentRef":
        return cls(
            document.id, document.filename, tuple(getattr(document, "sheet_names", ())),
            getattr(document, "is_current", True),
        )


_CASH_FLOW_TABLE = re.compile(r"cash flows?|flujos? de efectivo|flujos? de caja", re.IGNORECASE)


def to_mapped_input(item: LineItem, document, figure: ExtractedFigure) -> MappedInput:
    """Turn one extraction into a formula input, checking the label mapping.

    The label check here is authoritative: when the label is one of the
    item's accepted names, the extraction model's own doubt about the label
    ("'Net sales' may not be exactly revenue") is dropped."""
    if not figure.found:
        return MappedInput(
            key=item.key, name=item.name, statement=item.statement, found=False,
            document_id=str(document.id), document_filename=document.filename,
            reason=f"{document.filename}: {figure.reason or 'not found'}", error=figure.error,
        )
    if "%" in (figure.value_text or ""):
        # Every formula input is an amount; "27 %" is a growth rate or a margin.
        return MappedInput(
            key=item.key, name=item.name, statement=item.statement, found=False,
            document_id=str(document.id), document_filename=document.filename,
            reason=f"{document.filename}: doesn't report {item.name} as an amount (found a percentage).",
        )
    if names_other_item(item, figure.label):
        # "Operating income" offered as gross profit, or the balance sheet's
        # grand total as total liabilities: a different line, not this one.
        return MappedInput(
            key=item.key, name=item.name, statement=item.statement, found=False,
            document_id=str(document.id), document_filename=document.filename,
            reason=f"{document.filename}: doesn't report {item.name} (the closest line, "
                   f"'{figure.label}', is a different item).",
        )

    issues = dict(figure.issues) or ({"other": figure.reason} if figure.reason else {})
    # A scale the model claimed but the document doesn't state is already
    # ignored (the value is used as written). Ratios don't depend on it either:
    # within one document the scale cancels out, across documents only
    # documented scales are applied. So it's not something to review.
    issues.pop("scale", None)
    if item.statement == "balance_sheet" and figure.period_coverage not in (None, "point_in_time"):
        # A balance read from a column covering a span of time is most likely
        # the cash-flow change of the same line ("Inventory (6,696)").
        header = f" ({figure.column_header})" if figure.column_header else ""
        issues["coverage"] = (
            f"{item.name} is a balance-sheet amount, but this value covers "
            f"{figure.period_coverage.replace('_', ' ')}{header}; it may be a cash-flow change, not the balance."
        )
    source_text = figure.citation.text if figure.citation else ""
    table_title = source_text.split("row '", 1)[0] if "row '" in source_text else ""
    if item.statement == "balance_sheet" and _CASH_FLOW_TABLE.search(table_title):
        issues["coverage"] = (
            f"{item.name} was read from the cash flow statement, where it is the period's change, "
            "not the balance; check the balance sheet."
        )
    from_pdf = bool(figure.citation and figure.citation.page_number is not None)
    if (
        item.statement == "income_statement"
        and (from_pdf or _MULTI_PERIOD.search(source_text))
        and not column_of(figure.value_text, source_text)
    ):
        # Read from PDF text with no column labels: statements usually have
        # several period columns (quarter, year to date, prior year) and
        # nothing confirms which one this is — the model's own column claim
        # isn't evidence.
        issues["coverage"] = (
            f"{item.name} was read from text without column labels, so its period column couldn't be "
            "confirmed; check it's the right one."
        )
    needs_review = not label_matches(item, figure.label)
    if needs_review:
        issues.pop("label", None)
        confidence: Confidence = "low"
        shown = f"'{figure.label}'" if figure.label else "an unlabelled figure"
        reasons = [
            f"Mapped {item.name} to {shown}, which isn't a standard label for it. "
            "Check that it's the right line item before relying on ratios that use it.",
            *issues.values(),
        ]
    else:
        issues.pop("label", None)
        confidence = "high" if not issues else "medium"
        reasons = list(issues.values())

    return MappedInput(
        key=item.key, name=item.name, statement=item.statement, found=True,
        value=figure.value, value_text=figure.value_text, unit_scale=figure.unit_scale,
        currency=figure.currency, period=figure.period, label=figure.label, entity=figure.entity,
        period_coverage=figure.period_coverage, column_header=figure.column_header,
        document_id=str(document.id), document_filename=document.filename,
        citation=figure.citation, confidence=confidence, needs_review=needs_review,
        coverage_mismatch="coverage" in issues,
        reason=" ".join(reasons) or None,
    )


def order_documents(documents: list, item: LineItem) -> list:
    """Documents most likely to hold `item`'s statement first (stable otherwise)."""
    def names(document) -> list[str]:
        return [document.filename, *getattr(document, "sheet_names", [])]

    return sorted(documents, key=lambda d: -statement_hint_score(item.statement, names(d)))


@dataclass
class _Lookup:
    mapped: MappedInput
    attempts: int
    errors: int


def _find_item(
    db: Session | None, documents: list[DocumentRef], item: LineItem, period, user_roles, user_id, extractor
) -> _Lookup:
    """One input across the documents: stop at the first high-confidence
    mapping, otherwise keep the best one seen."""
    best: MappedInput | None = None
    misses: list[str] = []
    attempts = errors = 0
    for document in order_documents(documents, item):
        figure = extractor(
            db, document.id, item.query, user_roles=user_roles, user_id=user_id, period=period,
            labels=item.accepted_labels, exclude_labels=other_labels(item),
        )
        attempts += 1
        errors += figure.error
        candidate = to_mapped_input(item, document, figure)
        if not candidate.found:
            misses.append(candidate.reason)
            continue
        if best is None or _RANK[candidate.confidence] > _RANK[best.confidence]:
            best = candidate
        if best.confidence == "high":
            break
    mapped = best or MappedInput(
        key=item.key, name=item.name, statement=item.statement, found=False,
        reason=f"{item.name} wasn't found in the selected documents ({'; '.join(misses)}).",
        error=attempts > 0 and errors == attempts,
    )
    return _Lookup(mapped, attempts, errors)


def map_line_items(
    db: Session | None,
    documents: list,
    item_keys: list[str],
    period: str | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    extractor: Extractor = extract_figure,
    session_factory: Callable[[], Session] | None = None,
    max_workers: int = 1,
) -> dict[str, MappedInput]:
    """Find each input in the selected documents.

    With a `session_factory`, inputs are looked up concurrently (one model
    call each, so this is what keeps the whole library within a few seconds
    per document); each worker uses its own session and commits its
    retrieval audit entries. Without one, everything runs on `db`.

    Raises ExtractionServiceError when every extraction failed because the
    model service didn't respond, so the caller can say so instead of
    reporting every input as missing."""
    refs = [DocumentRef.of(d) for d in documents]
    items = [LINE_ITEMS[key] for key in item_keys]

    def lookup(item: LineItem) -> _Lookup:
        if session_factory is None:
            return _find_item(db, refs, item, period, user_roles, user_id, extractor)
        session = session_factory()
        try:
            found = _find_item(session, refs, item, period, user_roles, user_id, extractor)
            session.commit()
            return found
        finally:
            session.close()

    if session_factory is None or max_workers <= 1:
        lookups = [lookup(item) for item in items]
    else:
        # Each worker runs in a copy of this request's context, so its audit
        # entries keep the request's id and resource (app/audit.py).
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(contextvars.copy_context().run, lookup, item) for item in items]
            lookups = [future.result() for future in futures]

    attempts = sum(found.attempts for found in lookups)
    if attempts and sum(found.errors for found in lookups) == attempts:
        raise ExtractionServiceError("The language model service didn't respond. Try again in a moment.")
    return {found.mapped.key: found.mapped for found in lookups}


def _unique_citations(inputs: list[MappedInput]) -> list[ResolvedCitation]:
    seen, citations = set(), []
    for mapped in [part for i in inputs for part in (i.derived_from or [i])]:
        c = mapped.citation
        if c is None:
            continue
        identity = (c.document_id, c.sheet_name, c.cell_range, c.page_number, c.text)
        if identity not in seen:
            seen.add(identity)
            citations.append(c)
    return citations


def compute_ratio(definition: RatioDefinition, mapped: dict[str, MappedInput]) -> RatioResult:
    """Pure: the ratio from already-mapped inputs (see module docstring)."""
    inputs = [mapped[key] for key in definition.inputs]
    result = RatioResult(
        key=definition.key, name=definition.name, category=definition.category,
        formula=definition.formula, unit=definition.unit, description=definition.description,
        status="not_calculable", value=None, inputs=inputs,
        citations=_unique_citations(inputs),
        cross_document=len({i.document_id for i in inputs if i.found}) > 1,
    )

    missing = [i.name for i in inputs if not i.found]
    if missing:
        result.missing_inputs = missing
        result.reasons.append(f"Missing input: {', '.join(missing)}.")
        return result

    companies = _distinct_entities(inputs)
    if len(companies) > 1:
        result.reasons.append(
            f"The inputs come from different companies ({', '.join(companies)}); "
            "a ratio needs one company's statements."
        )
        return result

    currencies = sorted({i.currency for i in inputs if i.currency})
    if len(currencies) > 1:
        result.reasons.append(
            f"The inputs are in different currencies ({', '.join(currencies)}), so the ratio isn't meaningful."
        )
        return result
    result.currency = currencies[0] if currencies else None

    try:
        value = definition.compute(_operands(inputs))
    except ZeroDivisionError:
        result.reasons.append("The denominator is zero, so the ratio can't be calculated.")
        return result
    result.value = value
    result.status = "calculated"

    periods = distinct_periods([_comparable_period(i) for i in inputs])
    if len(periods) > 1:
        result.status = "needs_review"
        result.reasons.append(
            f"The inputs are for different periods ({', '.join(periods)}); check they belong together."
        )
    elif periods:
        result.period = periods[0]

    coverages = {
        i.period_coverage for i in inputs
        if i.statement == "income_statement" and i.period_coverage not in (None, "point_in_time")
    }
    if len(coverages) > 1:
        result.status = "needs_review"
        result.reasons.append(
            "The income-statement inputs cover different lengths of time ("
            + ", ".join(sorted(c.replace("_", " ") for c in coverages))
            + "); e.g. a nine-month figure over a quarter's figure."
        )

    for i in inputs:
        if i.key in POSITIVE_ITEMS and i.value < 0:
            result.status = "needs_review"
            result.reasons.append(
                f"{i.name} is negative ({i.value_text}), so this ratio isn't meaningful as usual; "
                "check the sign and the line item."
            )

    low, high = definition.plausible
    if (low is not None and value < low) or (high is not None and value > high):
        result.status = "needs_review"
        result.reasons.append(
            "The result is outside the plausible range for this ratio; the inputs may come from "
            "different periods or line items."
        )

    for i in inputs:
        if i.needs_review:
            result.status = "needs_review"
            result.reasons.append(f"{i.name} was mapped to '{i.label}' — low-confidence mapping, review it.")
        elif i.coverage_mismatch:
            # Likely the wrong column (e.g. nine months for a quarter): hold it.
            result.status = "needs_review"
            result.reasons.append(f"{i.name}: {i.reason}")
        elif i.confidence != "high" and i.reason:
            # Not enough to hold the ratio back, but the analyst should see it.
            result.reasons.append(f"{i.name}: {i.reason}")
    return result


_MULTI_PERIOD = re.compile(
    r"months ended|quarters? ended|year to date|year-to-date|meses terminados|trimestre terminado|acumulado",
    re.IGNORECASE,
)
_END_DATE = re.compile(
    r"[A-Z][a-z]+\.?\s+\d{1,2},?\s*(?:19|20)\d{2}"           # June 27, 2026
    r"|\d{1,2}\s+[A-Z][a-z]+\.?\s+(?:19|20)\d{2}"            # 31 December 2025
    r"|\d{1,2}\s+de\s+[A-Za-záéíóú]+\s+del?\s+(?:19|20)\d{2}"  # 31 de diciembre de 2025
)


def _comparable_period(i: MappedInput) -> str | None:
    """The period to compare inputs by: the end date in the column the value
    was read from when it has one ("Three Months Ended June 27, 2026" and the
    balance "June 27, 2026" are one period, even if the model called one of
    them "Q3 2026" — a fiscal quarter that a calendar can't tell), else the
    period the model reported."""
    if i.column_header:
        dates = _END_DATE.findall(i.column_header)
        if dates:
            return dates[-1]
        # A column headed by its year alone ("2025") is that year: compared
        # with "December 31, 2023" it is a different period.
        if re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", i.column_header):
            return i.column_header
    return i.period


def flag_odd_coverage(mapped: dict[str, MappedInput]) -> None:
    """Income-statement inputs should all cover the same span. When most come
    from the quarter and one from nine months (a statement with both kinds of
    column), hold the odd one out; on a tie, the quarter wins."""
    flows = [i for i in mapped.values() if i.found and i.statement == "income_statement" and i.period_coverage]
    counts: dict[str, int] = {}
    for i in flows:
        counts[i.period_coverage] = counts.get(i.period_coverage, 0) + 1
    if len(counts) < 2:
        return
    usual = max(counts, key=lambda c: (counts[c], c == "three_months"))
    for i in flows:
        if i.period_coverage != usual:
            i.coverage_mismatch = True
            note = (f"{i.name} covers {i.period_coverage.replace('_', ' ')} while the other income-statement "
                    f"inputs cover {usual.replace('_', ' ')}.")
            i.reason = f"{note} {i.reason}" if i.reason else note


# Inputs a statement often doesn't print as a line, but that follow from two
# it does: many US balance sheets have no "Total liabilities" line, and many
# income statements no "Gross profit". (input, minuend, subtrahend, formula)
_DERIVATIONS = {
    "total_liabilities": ("total_assets", "total_equity", "Total assets − Total equity"),
    # Cost of sales may be printed negative; gross profit subtracts its magnitude.
    "gross_profit": ("revenue", "cost_of_sales", "Revenue − Cost of sales"),
}


def _format_amount(value: float) -> str:
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text


def derive(key: str, mapped: dict[str, MappedInput]) -> MappedInput | None:
    """`key` computed from its two parts, when both were found in the same
    document, for the same period and span, as standard line items; None
    otherwise (then the input stays missing)."""
    minuend_key, subtrahend_key, formula = _DERIVATIONS[key]
    a, b = mapped.get(minuend_key), mapped.get(subtrahend_key)
    if a is None or b is None or not (a.found and b.found):
        return None
    if a.needs_review or b.needs_review or a.coverage_mismatch or b.coverage_mismatch:
        return None
    if a.document_id != b.document_id or a.unit_scale != b.unit_scale:
        return None
    if len(distinct_periods([_comparable_period(a), _comparable_period(b)])) > 1:
        return None
    if a.period_coverage and b.period_coverage and a.period_coverage != b.period_coverage:
        return None
    value = a.value - abs(b.value)
    scale = UNIT_SCALES.get(a.unit_scale, 1.0)
    item = LINE_ITEMS[key]
    return MappedInput(
        key=key, name=item.name, statement=item.statement, found=True,
        value=value, value_text=_format_amount(value / scale), unit_scale=a.unit_scale,
        currency=a.currency or b.currency, period=a.period or b.period, label=f"Derived: {formula}",
        entity=a.entity or b.entity, period_coverage=a.period_coverage or b.period_coverage,
        column_header=a.column_header or b.column_header,
        document_id=a.document_id, document_filename=a.document_filename, citation=a.citation,
        confidence="medium",
        reason=(f"{item.name} isn't reported as a line; derived as {formula} = "
                f"{a.value_text} − {b.value_text.lstrip('(').rstrip(')')}."),
        derived_from=[a, b],
    )


def _distinct_entities(inputs) -> list[str]:
    """The different companies among the inputs (unknown ones left out)."""
    seen: list[str] = []
    for i in inputs:
        if i.found and i.entity and entity_key(i.entity) and not any(same_entity(i.entity, s) for s in seen):
            seen.append(i.entity)
    return seen


def _operands(inputs: list[MappedInput]) -> dict[str, float]:
    """Formula operands. When every input comes from one document, use the
    figures as written: a ratio is unit-free, so the document's scale cancels
    out, and it avoids trusting a scale ("in millions") that extraction applied
    to some of its figures but not to others."""
    scales = {i.unit_scale for i in inputs}
    # Two explicit, different scales ("79,975" in millions, "$2.3 billion") are
    # both real: then the scaled values are the comparable ones.
    if len({i.document_id for i in inputs}) == 1 and (len(scales) == 1 or "units" in scales):
        return {i.key: i.value / UNIT_SCALES.get(i.unit_scale, 1.0) for i in inputs}
    return {i.key: i.value for i in inputs}


def select_ratios(ratio_keys: list[str] | None = None, categories: list[str] | None = None) -> list[RatioDefinition]:
    """The requested ratios (all by default), in library order."""
    unknown = set(ratio_keys or []) - RATIOS.keys()
    if unknown:
        raise ValueError(f"Unknown ratio(s): {', '.join(sorted(unknown))}")
    unknown = set(categories or []) - set(CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown categor(y/ies): {', '.join(sorted(unknown))}")
    return [
        definition for definition in RATIOS.values()
        if (not ratio_keys or definition.key in ratio_keys)
        and (not categories or definition.category in categories)
    ]


def calculate_ratios(
    db: Session | None,
    documents: list,
    period: str | None = None,
    ratio_keys: list[str] | None = None,
    categories: list[str] | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    extractor: Extractor = extract_figure,
    session_factory: Callable[[], Session] | None = None,
    max_workers: int = 1,
) -> RatioReport:
    with span("calculate_ratios", input={
        "period": period, "documents": [d.filename for d in documents], "ratios": ratio_keys, "categories": categories,
    }) as traced:
        report = _calculate_ratios(
            db, documents, period, ratio_keys, categories, user_roles, user_id, extractor, session_factory, max_workers,
        )
        traced.update(output={
            "ratios": {r.key: {"status": r.status, "value": r.value, "missing_inputs": r.missing_inputs,
                               "reasons": r.reasons} for r in report.ratios},
            "notes": report.notes,
        })
    return report


def _calculate_ratios(
    db, documents, period, ratio_keys, categories, user_roles, user_id, extractor, session_factory, max_workers,
) -> RatioReport:
    definitions = select_ratios(ratio_keys, categories)
    item_keys = list(dict.fromkeys(key for d in definitions for key in d.inputs))
    mapped = map_line_items(
        db, documents, item_keys, period=period, user_roles=user_roles, user_id=user_id,
        extractor=extractor, session_factory=session_factory, max_workers=max_workers,
    )
    flag_odd_coverage(mapped)
    missing = [key for key in _DERIVATIONS if key in mapped and not mapped[key].found and not mapped[key].error]
    parts = list(dict.fromkeys(
        part for key in missing for part in _DERIVATIONS[key][:2] if part not in mapped
    ))
    if parts:
        mapped.update(map_line_items(
            db, documents, parts, period=period, user_roles=user_roles, user_id=user_id,
            extractor=extractor, session_factory=session_factory, max_workers=max_workers,
        ))
    for key in missing:
        derived = derive(key, mapped)
        if derived is not None:
            mapped[key] = derived
    ratios = [compute_ratio(definition, mapped) for definition in definitions]

    notes = [
        f"{document.filename} is not the current version of its document."
        for document in documents if not getattr(document, "is_current", True)
    ]
    failed = [i.name for i in mapped.values() if i.error]
    if failed:
        notes.append(
            f"The language model service didn't respond for {', '.join(failed)}; "
            "calculate again to retry those inputs."
        )
    if any(i.needs_review for i in mapped.values()):
        notes.append("Some inputs were mapped to non-standard line items; ratios using them are held for review.")
    found = [i for i in mapped.values() if i.found]
    companies = _distinct_entities(found)
    if len(companies) > 1:
        notes.insert(0,
            f"The selected documents belong to different companies ({', '.join(companies)}). "
            "Ratios combine one company's statements: select only that company's documents."
        )
    if period is None and len(distinct_periods([_comparable_period(i) for i in found])) > 1:
        notes.append(
            "The statements contain several periods and the inputs didn't all come from the same one. "
            "Specify a period (e.g. FY2024 or Q3 2026) so every ratio uses figures from that period."
        )

    return RatioReport(
        period=period,
        document_ids=[str(d.id) for d in documents],
        ratios=ratios,
        line_items=list(mapped.values()),
        notes=notes,
    )
