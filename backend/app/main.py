from fastapi import FastAPI

from app.api.routes_ingest import router as ingest_router
from app.api.routes_query import router as query_router

app = FastAPI(title="FinDoc Retriever — Excel & PDF RAG")

app.include_router(ingest_router, tags=["ingestion"])
app.include_router(query_router, tags=["retrieval"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
