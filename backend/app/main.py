import atexit
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_audit import router as audit_router
from app.api.routes_compare import router as compare_router
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_documents import router as documents_router
from app.api.routes_ingest import router as ingest_router
from app.api.routes_oauth import router as oauth_router
from app.api.routes_query import router as query_router
from app.api.routes_ratios import router as ratios_router
from app.api.routes_sync import router as sync_router
from app.audit import AuditContextMiddleware
from app.config import get_settings
from app.observability import flush as flush_traces
from app.sync.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ID-HU-INT-001: sync connected SharePoint / Google Drive folders every
    # SYNC_INTERVAL_MINUTES while the API runs.
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="FinDoc Retriever — Excel & PDF RAG", lifespan=lifespan)
# Send buffered Langfuse spans before the process exits (app/observability.py).
atexit.register(flush_traces)

# ID-HU-BE-014: a request id and origin for every audit entry of a request.
app.add_middleware(AuditContextMiddleware)
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
app.include_router(ratios_router, tags=["ratios"])
app.include_router(audit_router, tags=["audit"])
app.include_router(dashboard_router, tags=["dashboard"])
app.include_router(sync_router, tags=["sync"])
app.include_router(oauth_router, tags=["sync"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
