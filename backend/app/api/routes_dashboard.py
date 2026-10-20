"""ID-HU-FE-004: key financial figures and ratios dashboard."""
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.routes_compare import XLSX_MEDIA_TYPE
from app.config import get_settings
from app.dashboard.catalog import build_catalog
from app.dashboard.export import dashboard_workbook
from app.dashboard.service import DashboardError, get_dashboard, refresh
from app.db.session import SessionLocal, get_db
from app.figures.extraction import ExtractionServiceError
from app.schemas import (
    CatalogDocumentOut, CatalogEntityOut, DashboardCatalogOut, DashboardOut, DashboardRefreshRequest,
)

router = APIRouter()


@router.get("/dashboard/catalog", response_model=DashboardCatalogOut)
def dashboard_catalog(
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> DashboardCatalogOut:
    """The entities the dashboard can show, with the periods and statements
    found in their current documents (only documents the role may see)."""
    entities = []
    for entity in build_catalog(db):
        visible = entity.visible_to(x_user_role)
        if visible is None:
            continue
        entities.append(CatalogEntityOut(
            entity=visible.entity, key=visible.key, periods=[p.label for p in visible.periods],
            documents=[
                CatalogDocumentOut(id=d.id, filename=d.filename, statements=d.statements,
                                   periods=[p.label for p in d.periods])
                for d in visible.documents
            ],
        ))
    return DashboardCatalogOut(entities=entities)


@router.get("/dashboard", response_model=DashboardOut)
def read_dashboard(
    entity: str,
    periods: Annotated[list[str] | None, Query()] = None,
    categories: Annotated[list[str] | None, Query()] = None,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
    db: Session = Depends(get_db),
) -> DashboardOut:
    """The stored ratios of `entity` by period, with their sources, outliers
    and what's stale. No model calls: POST /dashboard/refresh calculates."""
    try:
        return get_dashboard(db, entity, periods=periods, categories=categories,
                             user_roles=x_user_role, user_id=x_user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dashboard/refresh", response_model=DashboardOut)
def refresh_dashboard(
    request: DashboardRefreshRequest,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
    db: Session = Depends(get_db),
) -> DashboardOut:
    """Calculate and store the entity's ratios for each period (all the
    periods found in its documents by default). Takes a few seconds per
    period: one model call per input, as in POST /ratios."""
    try:
        return refresh(
            db, request.entity, periods=request.periods, user_roles=x_user_role, user_id=x_user_id,
            session_factory=SessionLocal, max_workers=get_settings().ratio_extraction_workers,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractionServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/dashboard/export.xlsx")
def export_dashboard(dashboard: DashboardOut) -> Response:
    """Excel export of the dashboard the analyst is looking at (posted back
    as returned by GET /dashboard), sources and outliers included."""
    slug = re.sub(r"[^a-z0-9]+", "-", dashboard.entity.lower()).strip("-") or "entity"
    return Response(
        content=dashboard_workbook(dashboard),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="ratios-dashboard-{slug}.xlsx"'},
    )
