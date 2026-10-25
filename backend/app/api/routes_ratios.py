"""ID-HU-BE-010: standard financial ratios from ingested statements."""
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dashboard.service import DashboardError, get_trend
from app.db.models import Document, DocumentStatus
from app.db.session import SessionLocal, get_db
from app.observability import trace
from app.figures.extraction import ExtractionServiceError
from app.ratios.engine import calculate_ratios
from app.ratios.formulas import RATIOS
from app.schemas import RatioDefinitionOut, RatioReportOut, RatioRequest, RatioTrendOut

router = APIRouter()


@router.get("/ratios/definitions", response_model=list[RatioDefinitionOut])
def list_ratio_definitions() -> list[RatioDefinitionOut]:
    """The formula library: every ratio, its category, formula and inputs."""
    return [
        RatioDefinitionOut(
            key=d.key, name=d.name, category=d.category, formula=d.formula,
            unit=d.unit, description=d.description, inputs=list(d.inputs),
        )
        for d in RATIOS.values()
    ]


@router.get("/ratios/trend", response_model=RatioTrendOut)
def ratio_trend(
    entity: str,
    ratio: str,
    periods: Annotated[list[str] | None, Query()] = None,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
    db: Session = Depends(get_db),
) -> RatioTrendOut:
    """ID-HU-BE-011: one ratio of `entity` across its stored periods (POST
    /dashboard/refresh stores them), in chronological order with the sources
    of each value. A period it couldn't be calculated for — an input or a
    statement missing, or no document at all for that quarter or year — is
    returned as `not_calculable` with its `missing_inputs`, never omitted."""
    try:
        return get_trend(db, entity, ratio, periods=periods, user_roles=x_user_role, user_id=x_user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ratios", response_model=RatioReportOut)
def compute_ratios(
    request: RatioRequest,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
    db: Session = Depends(get_db),
) -> RatioReportOut:
    """Calculate liquidity, solvency, profitability and efficiency ratios from
    the selected statements. Each input is looked up across all of them (so
    ROA can combine the income statement and the balance sheet) and every
    ratio carries citations to the line items it used.

    Ratios whose inputs were mapped to non-standard labels come back as
    `needs_review`; ones missing an input, mixing currencies or dividing by
    zero come back as `not_calculable` with the reason, and the inputs that
    weren't found in `missing_inputs` (ID-HU-BE-011)."""
    if len(set(request.document_ids)) != len(request.document_ids):
        raise HTTPException(status_code=400, detail="Each document can only be selected once.")

    documents = []
    for document_id in request.document_ids:
        document = db.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        if document.status == DocumentStatus.removed.value:  # ID-HU-INT-002
            raise HTTPException(status_code=410, detail=f"{document.filename} was removed from its repository.")
        documents.append(document)

    try:
        with trace("ratios", input=request.model_dump(mode="json"), user_id=x_user_id,
                   metadata={"roles": x_user_role}, tags=["ratios"]):
            report = calculate_ratios(
                db, documents, period=request.period, ratio_keys=request.ratios,
                categories=request.categories, user_roles=x_user_role, user_id=x_user_id,
                session_factory=SessionLocal, max_workers=get_settings().ratio_extraction_workers,
            )
    except ExtractionServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()  # persist the retrieval audit entries (ID-HU-BE-013)
    return RatioReportOut.model_validate(report)
