from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.retrieval.service import retrieve
from app.schemas import QueryRequest, QueryResponse

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, db: Session = Depends(get_db)) -> QueryResponse:
    results = retrieve(db, question=request.question, top_k=request.top_k)
    return QueryResponse(question=request.question, results=results)
