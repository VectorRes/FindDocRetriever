"""ID-HU-FE-003: compare one metric across documents/periods side by side."""
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.session import get_db
from app.figures.comparison import compare_documents
from app.figures.export import comparison_workbook
from app.schemas import CompareRequest, ComparisonOut

router = APIRouter()

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/compare", response_model=ComparisonOut)
def compare(
    request: CompareRequest,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
    db: Session = Depends(get_db),
) -> ComparisonOut:
    """Extract `metric` from each document (in the given order; the first is
    the baseline) and compare: values side by side with their citations,
    variance (absolute and %) against the baseline, whether they reconcile,
    documented exchange rates when currencies differ, and an escalation
    suggestion when they don't reconcile.

    Each value is verified to appear in its cited source; a document whose
    figure can't be found or verified shows as "not found" rather than a guess.
    Documents the role can't access are reported as restricted."""
    if len(set(request.document_ids)) != len(request.document_ids):
        raise HTTPException(status_code=400, detail="Each document can only be selected once.")

    documents = []
    for document_id in request.document_ids:
        document = db.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        documents.append(document)

    comparison = compare_documents(
        db, documents, request.metric.strip(), period=request.period,
        user_roles=x_user_role, user_id=x_user_id,
    )
    db.commit()  # persist the retrieval audit entries (ID-HU-BE-013)
    return ComparisonOut.model_validate(comparison)


@router.post("/compare/export.xlsx")
def export_comparison(comparison: ComparisonOut) -> Response:
    """Excel export of the comparison the analyst is looking at (posted back
    as returned by POST /compare), citations included."""
    slug = re.sub(r"[^a-z0-9]+", "-", comparison.metric.lower()).strip("-") or "metric"
    return Response(
        content=comparison_workbook(comparison),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="comparison-{slug}.xlsx"'},
    )
