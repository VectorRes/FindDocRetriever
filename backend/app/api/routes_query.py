from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, PdfReference, PdfTable
from app.db.session import get_db
from app.qa.service import answer_question
from app.retrieval.service import retrieve
from app.schemas import AnswerResponse, PdfStructureResponse, QueryRequest, QueryResponse

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, db: Session = Depends(get_db)) -> QueryResponse:
    results = retrieve(db, question=request.question, top_k=request.top_k)
    return QueryResponse(question=request.question, results=results)


@router.post("/query/answer", response_model=AnswerResponse)
def query_answer(request: QueryRequest, db: Session = Depends(get_db)) -> AnswerResponse:
    """ID-HU-BE-008: retrieve sources and generate a grounded, cited answer.

    Also implements the follow-up context part of ID-HU-BE-008/FE-001: pass
    `session_id` from the previous response to keep the conversation's context.
    """
    return answer_question(
        db, question=request.question, top_k=request.top_k, session_id=request.session_id
    )


@router.get("/documents/{document_id}/pdf-structure", response_model=PdfStructureResponse)
def pdf_structure(document_id: str, db: Session = Depends(get_db)) -> PdfStructureResponse:
    try:
        from uuid import UUID
        document_uuid = UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document id") from exc

    document = db.get(Document, document_uuid)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.doc_type != "pdf":
        raise HTTPException(status_code=400, detail="Document is not a PDF")

    tables = list(db.scalars(select(PdfTable).where(PdfTable.document_id == document_uuid).order_by(PdfTable.page_number, PdfTable.table_index)))
    references = list(db.scalars(select(PdfReference).where(PdfReference.document_id == document_uuid).order_by(PdfReference.page_number, PdfReference.reference_type, PdfReference.reference_number)))
    return PdfStructureResponse(document_id=document_uuid, tables=tables, references=references)
