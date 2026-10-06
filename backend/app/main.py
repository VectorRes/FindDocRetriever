from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_compare import router as compare_router
from app.api.routes_documents import router as documents_router
from app.api.routes_ingest import router as ingest_router
from app.api.routes_query import router as query_router
from app.config import get_settings

app = FastAPI(title="FinDoc Retriever — Excel & PDF RAG")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router, tags=["ingestion"])
app.include_router(query_router, tags=["retrieval"])
app.include_router(documents_router, tags=["documents"])
app.include_router(compare_router, tags=["comparison"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
