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
       a zero denominator                                   -> unavailable.

The model is only involved inside `extract_figure` (one call per input and
document tried), whose values are verified to appear in their source.
"""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.figures.extraction import UNIT_SCALES, ExtractedFigure, ExtractionServiceError, distinct_periods, entity_key, extract_figure
from app.qa.schemas import ResolvedCitation
from app.ratios.formulas import CATEGORIES, LINE_ITEMS, POSITIVE_ITEMS, RATIOS, LineItem, RatioDefinition
from app.ratios.mapping import label_matches, statement_hint_score

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


@dataclass
class RatioResult:
    key: str
    name: str
    category: str
    formula: str
    unit: str
    description: str
    # "calculated" | "needs_review" (value shown only after the analyst reviews
    # the flagged inputs) | "unavailable" (value is None).
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
        figure = extractor(db, document.id, item.query, user_roles=user_roles, user_id=user_id, period=period)
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
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            lookups = list(pool.map(lookup, items))

    attempts = sum(found.attempts for found in lookups)
    if attempts and sum(found.errors for found in lookups) == attempts:
        raise ExtractionServiceError("The language model service didn't respond. Try again in a moment.")
    return {found.mapped.key: found.mapped for found in lookups}


def _unique_citations(inputs: list[MappedInput]) -> list[ResolvedCitation]:
    seen, citations = set(), []
    for mapped in inputs:
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
        status="unavailable", value=None, inputs=inputs,
        citations=_unique_citations(inputs),
        cross_document=len({i.document_id for i in inputs if i.found}) > 1,
    )

    missing = [i.name for i in inputs if not i.found]
    if missing:
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

    periods = distinct_periods([i.period for i in inputs])
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


def _distinct_entities(inputs) -> list[str]:
    """The different companies among the inputs (unknown ones left out)."""
    seen: dict[str, str] = {}
    for i in inputs:
        if i.found and i.entity and entity_key(i.entity):
            seen.setdefault(entity_key(i.entity), i.entity)
    return list(seen.values())


def _operands(inputs: list[MappedInput]) -> dict[str, float]:
    """Formula operands. When every input comes from one document, use the
    figures as written: a ratio is unit-free, so the document's scale cancels
    out, and it avoids trusting a scale ("in millions") that extraction applied
    to some of its figures but not to others."""
    if len({i.document_id for i in inputs}) == 1:
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
    definitions = select_ratios(ratio_keys, categories)
    item_keys = list(dict.fromkeys(key for d in definitions for key in d.inputs))
    mapped = map_line_items(
        db, documents, item_keys, period=period, user_roles=user_roles, user_id=user_id,
        extractor=extractor, session_factory=session_factory, max_workers=max_workers,
    )
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
    if period is None and len(distinct_periods([i.period for i in found])) > 1:
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
