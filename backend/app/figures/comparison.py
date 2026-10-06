"""Cross-document comparison of one metric (ID-HU-FE-003).

`compare_documents` extracts the metric from each selected document (one
`extract_figure` call each) and looks up documented exchange rates when the
currencies differ. `summarize` then does all the arithmetic deterministically
(no model involved): values side by side and variance against the first
document (the baseline).

Two kinds of comparison, told apart by the periods of the extracted values:
  - same period (e.g. net income in the Q1 income statement vs the Q1 cash
    flow statement): the values should be equal, so the result says whether
    they reconcile and suggests escalating when they don't;
  - different periods (e.g. revenue 2024 vs 2025): a change is expected, so
    only the variance is shown — no reconciliation, no escalation.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Document
from app.escalation import EscalationSuggestion, suggest_escalation
from app.figures.extraction import ExchangeRate, ExtractedFigure, extract_figure, find_exchange_rate
from app.qa.schemas import ResolvedCitation


@dataclass
class ComparisonRow:
    document_id: str
    filename: str
    version_number: int
    is_current: bool
    is_baseline: bool
    found: bool
    value: float | None = None
    value_text: str | None = None
    unit_scale: str = "units"
    currency: str | None = None
    period: str | None = None
    label: str | None = None
    citation: ResolvedCitation | None = None
    confidence: str = "low"
    reason: str | None = None
    # The value in the comparison currency (= value when no conversion was needed).
    comparable_value: float | None = None
    converted: bool = False
    # Against the baseline row, in the comparison currency.
    variance_abs: float | None = None
    variance_pct: float | None = None
    matches_baseline: bool | None = None


@dataclass
class Comparison:
    metric: str
    period: str | None
    rows: list[ComparisonRow]
    # True: every value matches; False: a discrepancy; None: not applicable or
    # can't tell (values across periods, fewer than two comparable values, or
    # a currency with no documented rate).
    reconciles: bool | None
    # The values are for different periods: variance only, no reconciliation.
    across_periods: bool = False
    comparison_currency: str | None = None
    exchange_rates: list[ExchangeRate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    escalation: EscalationSuggestion | None = None


def _period_key(period: str | None) -> str | None:
    return " ".join(period.lower().split()) if period else None


def _within(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance * max(abs(a), abs(b), 1.0)


def summarize(
    metric: str,
    period: str | None,
    documents: list[Document],
    figures: list[ExtractedFigure],
    exchange_rates: dict[str, ExchangeRate],
    tolerance: float,
) -> Comparison:
    """Pure comparison arithmetic. `documents[i]` and `figures[i]` describe the
    same document; the first one is the baseline. `exchange_rates` maps a
    currency to its documented rate into the comparison currency."""
    found_currencies = [f.currency for f in figures if f.found and f.currency]
    baseline = figures[0]
    target = baseline.currency if baseline.found and baseline.currency else (found_currencies or [None])[0]

    rows: list[ComparisonRow] = []
    notes: list[str] = []
    missing_rate = False
    for index, (document, figure) in enumerate(zip(documents, figures)):
        row = ComparisonRow(
            document_id=str(document.id),
            filename=document.filename,
            version_number=document.version_number,
            is_current=document.is_current,
            is_baseline=index == 0,
            found=figure.found,
            value=figure.value,
            value_text=figure.value_text,
            unit_scale=figure.unit_scale,
            currency=figure.currency,
            period=figure.period,
            label=figure.label,
            citation=figure.citation,
            confidence=figure.confidence,
            reason=figure.reason,
        )
        if figure.found:
            if figure.currency is None or target is None or figure.currency == target:
                row.comparable_value = figure.value
            elif figure.currency in exchange_rates:
                row.comparable_value = figure.value * exchange_rates[figure.currency].rate
                row.converted = True
            else:
                missing_rate = True
        else:
            notes.append(f"{document.filename}: {figure.reason or 'figure not found'}")
        if not document.is_current:
            notes.append(f"{document.filename} is not the current version of its document.")
        rows.append(row)

    periods = [_period_key(row.period) for row in rows if row.found]
    across_periods = len(periods) > 1 and all(periods) and len(set(periods)) > 1

    base_value = rows[0].comparable_value
    if base_value is not None:
        for row in rows[1:]:
            if row.comparable_value is None:
                continue
            row.variance_abs = row.comparable_value - base_value
            row.variance_pct = (row.variance_abs / abs(base_value) * 100) if base_value else None
            if not across_periods:
                row.matches_baseline = _within(row.comparable_value, base_value, tolerance)

    comparable = [row for row in rows if row.comparable_value is not None]
    if missing_rate:
        currencies = sorted({c for c in found_currencies if c != target})
        notes.append(
            f"No documented exchange rate was found for {', '.join(currencies)} to {target}; "
            "those values are shown in their original currency and not converted."
        )
    if across_periods:
        notes.append("The values are for different periods: the variance is shown, no reconciliation is expected.")
        reconciles = None
    elif missing_rate or len(comparable) < 2:
        reconciles = None
    else:
        reconciles = all(_within(row.comparable_value, comparable[0].comparable_value, tolerance) for row in comparable)

    escalation = None
    if reconciles is False:
        escalation = suggest_escalation(
            "consolidation",
            f"The {metric} figures don't reconcile across the selected documents.",
        )

    return Comparison(
        metric=metric,
        period=period,
        rows=rows,
        reconciles=reconciles,
        across_periods=across_periods,
        comparison_currency=target,
        exchange_rates=list(exchange_rates.values()),
        notes=notes,
        escalation=escalation,
    )


def compare_documents(
    db: Session,
    documents: list[Document],
    metric: str,
    period: str | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
) -> Comparison:
    figures = [
        extract_figure(db, document.id, metric, user_roles=user_roles, user_id=user_id, period=period)
        for document in documents
    ]

    baseline_currency = figures[0].currency if figures[0].found else None
    currencies = [f.currency for f in figures if f.found and f.currency]
    target = baseline_currency or (currencies[0] if currencies else None)
    rates: dict[str, ExchangeRate] = {}
    for currency in dict.fromkeys(currencies):
        if target and currency != target:
            rate = find_exchange_rate(db, currency, target, user_roles=user_roles, user_id=user_id)
            if rate is not None:
                rates[currency] = rate

    return summarize(metric, period, documents, figures, rates, get_settings().comparison_tolerance)
