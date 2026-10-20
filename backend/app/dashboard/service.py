"""Key ratios dashboard (ID-HU-FE-004): stored calculations, trends, outliers.

- `refresh` calculates an entity's ratios for each period with BE-010's
  engine (one `calculate_ratios` per period, on the entity's documents for
  that period) and stores the result as a DashboardRun with one
  RatioSnapshot per ratio. A ratio needing a statement the entity has no
  document for in that period isn't sent to the engine: it's stored as
  "unavailable" with the missing statement ("insufficient data").
- `get_dashboard` reads the stored runs — no model calls — and builds each
  ratio's trend, flags outliers (outliers.py) and marks runs that are stale
  (their documents are no longer the entity's current ones for the period)
  or restricted (the viewer can't see one of their documents: no values,
  and the attempt is logged, ID-HU-BE-014).
"""
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import Denial, record_denials
from app.config import get_settings
from app.dashboard.catalog import (
    STATEMENT_NAMES, CatalogEntity, Period, build_catalog, find_entity, parse_period,
)
from app.dashboard.outliers import detect_outliers, explain
from app.figures.extraction import extract_figure
from app.ratios.engine import Extractor, calculate_ratios
from app.ratios.formulas import CATEGORIES, LINE_ITEMS, RATIOS, RatioDefinition
from app.retrieval.access import can_access, parse_roles
from app.schemas import (
    CitationOut, DashboardDocumentOut, DashboardOut, DashboardPeriodOut, DashboardPointOut,
    DashboardRatioOut, OutlierOut, RatioInputOut,
)

class DashboardError(ValueError):
    """A request the dashboard can't serve (unknown entity, no periods…)."""


def _needed_statements(definition: RatioDefinition) -> list[str]:
    return list(dict.fromkeys(LINE_ITEMS[key].statement for key in definition.inputs))


def _insufficient_reason(missing: list[str], period: str, entity: str) -> str:
    names = " and the ".join(STATEMENT_NAMES[s] for s in missing)
    return f"Insufficient data: the {names} for {period} hasn't been ingested for {entity}."


def _resolve_periods(entity: CatalogEntity, requested: list[str] | None) -> list[Period]:
    if requested:
        periods = []
        for text in requested:
            period = parse_period(text)
            if period is None:
                raise DashboardError(f"'{text}' isn't a period the dashboard understands (e.g. 2024, FY2024, Q3 2026).")
            periods.append(period)
        return sorted(set(periods))
    periods = entity.periods
    if not periods:
        raise DashboardError(
            f"No periods were found in {entity.entity}'s documents; enter the periods to calculate (e.g. 2024)."
        )
    return periods[-get_settings().dashboard_max_periods:]


def refresh(
    db: Session,
    entity_name: str,
    periods: list[str] | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    extractor: Extractor = extract_figure,
    session_factory: Callable[[], Session] | None = None,
    max_workers: int = 1,
) -> DashboardOut:
    """Calculate and store `entity_name`'s ratios for each period, then return
    the dashboard. Each period is committed as soon as it's calculated, so a
    model failure later on keeps the periods already done; it raises
    ExtractionServiceError (the API answers 503)."""
    from app.db.models import DashboardRun, Document, RatioSnapshot

    catalog = build_catalog(db)
    full = find_entity(catalog, entity_name)
    entity = full.visible_to(user_roles) if full else None
    if entity is None:
        raise LookupError(f"No documents found for '{entity_name}'.")
    if len(_resolve_periods(entity, periods)) > get_settings().dashboard_max_periods:
        raise DashboardError(f"Choose at most {get_settings().dashboard_max_periods} periods.")

    for period in _resolve_periods(entity, periods):
        documents = entity.documents_for(period)
        held = entity.statements_for(period) if documents else set()
        definitions = list(RATIOS.values())
        missing = {d.key: [s for s in _needed_statements(d) if s not in held] for d in definitions}
        computable = [d.key for d in definitions if not missing[d.key]]

        results, notes = {}, []
        if computable:
            orm_documents = [db.get(Document, document.id) for document in documents]
            report = calculate_ratios(
                db, orm_documents, period=period.label, ratio_keys=computable,
                user_roles=user_roles, user_id=user_id, extractor=extractor,
                session_factory=session_factory, max_workers=max_workers,
            )
            results = {ratio.key: ratio for ratio in report.ratios}
            notes = list(report.notes)
        if not documents:
            notes.append(f"No document of {entity.entity} covers {period.label}.")

        for old in db.scalars(
            select(DashboardRun).where(DashboardRun.entity_key == entity.key, DashboardRun.period == period.label)
        ):
            db.delete(old)
        db.flush()
        run = DashboardRun(
            entity=entity.entity, entity_key=entity.key, period=period.label, period_sort=period.sort,
            document_ids=[d.id for d in documents], notes=notes,
            computed_at=datetime.now(timezone.utc), computed_by=user_id or "anonymous",
        )
        for position, definition in enumerate(definitions):
            result = results.get(definition.key)
            if result is None:
                snapshot = RatioSnapshot(
                    position=position, ratio_key=definition.key, category=definition.category,
                    status="unavailable", value=None, inputs=[], citations=[], cross_document=False,
                    reasons=[_insufficient_reason(missing[definition.key], period.label, entity.entity)],
                    missing_statements=missing[definition.key],
                )
            else:
                snapshot = RatioSnapshot(
                    position=position, ratio_key=definition.key, category=definition.category,
                    status=result.status, value=result.value, currency=result.currency,
                    inputs=[RatioInputOut.model_validate(i).model_dump(mode="json") for i in result.inputs],
                    citations=[CitationOut.model_validate(c).model_dump(mode="json") for c in result.citations],
                    cross_document=result.cross_document, reasons=list(result.reasons), missing_statements=[],
                )
            run.snapshots.append(snapshot)
        db.add(run)
        db.commit()

    return get_dashboard(db, entity.entity, user_roles=user_roles, user_id=user_id)


def get_dashboard(
    db: Session,
    entity_name: str,
    periods: list[str] | None = None,
    categories: list[str] | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
) -> DashboardOut:
    """The stored dashboard of `entity_name` (see module docstring)."""
    from app.db.models import DashboardRun, Document

    unknown = set(categories or []) - set(CATEGORIES)
    if unknown:
        raise DashboardError(f"Unknown categor(y/ies): {', '.join(sorted(unknown))}")
    catalog = build_catalog(db)
    full = find_entity(catalog, entity_name)
    entity = full.visible_to(user_roles) if full else None
    if full is None or entity is None:
        raise LookupError(f"No documents found for '{entity_name}'.")

    runs = {run.period: run for run in db.scalars(
        select(DashboardRun).where(DashboardRun.entity_key == full.key).order_by(DashboardRun.period_sort)
    )}
    wanted = {p.label for p in map(parse_period, periods or []) if p} if periods else None
    all_periods: dict[str, str] = {run.period: run.period_sort for run in runs.values()}
    for period in entity.periods:
        all_periods.setdefault(period.label, period.sort)
    ordered = [p for p, _ in sorted(all_periods.items(), key=lambda item: item[1]) if wanted is None or p in wanted]

    roles = parse_roles(user_roles)
    period_views: list[DashboardPeriodOut] = []
    usable: dict[str, object] = {}  # period -> run whose values the viewer may see
    for label in ordered:
        run = runs.get(label)
        if run is None:
            period_views.append(DashboardPeriodOut(period=label, calculated=False))
            continue
        documents = [db.get(Document, _uuid(d)) for d in run.document_ids]
        # Stale against what this viewer would calculate from now: a run made
        # from documents someone else can't see isn't "stale" for them.
        current = {d.id for d in entity.documents_for(Period(all_periods[label], label))}
        stale = set(run.document_ids) != current or any(d is None for d in documents)
        denied = [d for d in documents if d is not None and not can_access(d.confidentiality_tag, roles)]
        if denied:
            record_denials(
                user_id=user_id, roles=roles, question=f"Dashboard: {entity.entity} {label}",
                denials=[Denial(document_id=d.id, document_filename=d.filename, tag=d.confidentiality_tag,
                                action="view_dashboard") for d in denied],
            )
        else:
            usable[label] = run
        period_views.append(DashboardPeriodOut(
            period=label, calculated=True, stale=stale, restricted=bool(denied),
            computed_at=run.computed_at, computed_by=run.computed_by,
            documents=[DashboardDocumentOut(id=str(d.id), filename=d.filename)
                       for d in documents if d is not None and d not in denied],
            notes=list(run.notes) if not denied else [],
        ))
    stale_periods = {p.period for p in period_views if p.stale}

    snapshots = {
        label: {s.ratio_key: s for s in run.snapshots} for label, run in usable.items()
    }
    calculated = {
        label: {key: s.value for key, s in by_key.items() if s.status == "calculated" and s.value is not None}
        for label, by_key in snapshots.items()
    }

    ratios: list[DashboardRatioOut] = []
    for definition in RATIOS.values():
        if categories and definition.category not in categories:
            continue
        points: list[DashboardPointOut] = []
        for view in period_views:
            snapshot = snapshots.get(view.period, {}).get(definition.key)
            if view.restricted:
                points.append(DashboardPointOut(
                    period=view.period, status="restricted",
                    reasons=["Calculated from a document your role can't access."],
                ))
            elif snapshot is None:
                points.append(DashboardPointOut(period=view.period, status="not_calculated"))
            else:
                points.append(DashboardPointOut(
                    period=view.period, status=snapshot.status, value=snapshot.value, currency=snapshot.currency,
                    inputs=snapshot.inputs, citations=snapshot.citations, cross_document=snapshot.cross_document,
                    reasons=list(snapshot.reasons), missing_statements=list(snapshot.missing_statements),
                    stale=view.period in stale_periods,
                ))

        series = [(p.period, p.value) for p in points if p.status == "calculated" and p.value is not None]
        for period, outlier in detect_outliers(series, definition.unit).items():
            point = next(p for p in points if p.period == period)
            previous = next((p for p in points if p.period == outlier.previous_period), None)
            explain(
                definition.key, definition.name, outlier, period,
                calculated.get(outlier.previous_period, {}), calculated.get(period, {}),
                [i.model_dump() for i in previous.inputs] if previous else [],
                [i.model_dump() for i in point.inputs], definition.unit,
            )
            point.outlier = OutlierOut.model_validate(outlier)
        ratios.append(DashboardRatioOut(
            key=definition.key, name=definition.name, category=definition.category, formula=definition.formula,
            unit=definition.unit, description=definition.description, points=points,
        ))

    notes = []
    if stale_periods:
        notes.append(
            f"The documents for {', '.join(sorted(stale_periods, key=lambda p: all_periods[p]))} changed since "
            "they were calculated (a new version, upload or deletion): refresh to recalculate."
        )
    if not runs:
        notes.append("Nothing has been calculated for this entity yet: refresh to calculate its ratios.")
    return DashboardOut(entity=entity.entity, entity_key=full.key, periods=period_views, ratios=ratios, notes=notes)


def _uuid(value):
    from uuid import UUID

    try:
        return UUID(str(value))
    except ValueError:
        return None
