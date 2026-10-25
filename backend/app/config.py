from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres
    database_url: str = "postgresql+psycopg://findoc:findoc@localhost:5432/findoc_rag"

    # Embeddings
    embedding_provider: Literal["openai", "local"] = "openai"

    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 1536

    # QA generation agent (LangGraph + LangChain, backed by the OpenAI chat API)
    openai_chat_model: str = "gpt-4o-mini"
    qa_temperature: float = 0.0

    local_embedding_model: str = "BAAI/bge-m3"
    local_embedding_dimensions: int = 1024

    # PDF ingestion: native text extraction first, OCR fallback for scanned pages.
    pdf_chunk_size: int = 1000
    pdf_chunk_overlap: int = 150
    pdf_ocr_enabled: bool = True
    pdf_ocr_dpi: int = 300
    pdf_ocr_language: str = "eng+spa"
    # OCR is used when native extraction yields fewer than this many non-space chars.
    # Keep at 1 to OCR only image-only pages; raise it for PDFs with broken text layers.
    pdf_ocr_min_native_chars: int = 1
    # OCR text whose average per-word Tesseract confidence (0-1) falls below this
    # threshold is tagged "low confidence — verify against scan" when indexed.
    pdf_ocr_low_confidence_threshold: float = 0.70

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    retrieval_top_k: int = 8
    # ID-HU-BE-007: chunks less similar than this to the question (cosine
    # similarity, 0-1) are not relevant and aren't retrieved; 0 disables it.
    # Calibrated for text-embedding-3-small: off-topic questions score <= 0.18
    # against financial statements, questions they answer >= 0.33.
    retrieval_min_score: float = 0.25
    # ID-HU-FE-003: relative difference below which compared values count as
    # reconciling (0.001 = 0.1%, absorbs rounding but flags a 0.5% gap).
    comparison_tolerance: float = 0.001
    # ID-HU-BE-010: ratio inputs extracted concurrently (one model call each).
    # Keep it within the chat model provider's rate limit.
    ratio_extraction_workers: int = 6
    # QA: also search each question in the other language (English <-> Spanish).
    query_translation: bool = True
    # ID-HU-FE-004: a ratio is flagged as an outlier when it moves more than
    # this share of its previous value (0.5 = 50%) and more than the minimum
    # absolute change for its unit (5 percentage points, or 0.5x).
    dashboard_outlier_rel_change: float = 0.5
    dashboard_outlier_min_points: float = 0.05
    dashboard_outlier_min_times: float = 0.5
    # Most periods one dashboard refresh calculates (each costs model calls).
    dashboard_max_periods: int = 8

    # Tracing of the agent with Langfuse (app/observability.py): off unless
    # both keys are set. The base URL is the self-hosted langfuse-web service.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "http://langfuse-web:3000"
    langfuse_environment: str = "development"
    langfuse_tracing_enabled: bool = True

    # ID-HU-INT-001: automatic sync of connected SharePoint / Google Drive
    # folders (app/sync/). Every this many minutes the API checks every
    # connected folder for new and updated files; 0 turns the scheduler off
    # (POST /sync/run still syncs on demand).
    sync_interval_minutes: int = 15
    # SharePoint, through Microsoft Graph with an Entra ID (Azure AD) app
    # registration: client-credentials flow, application permission
    # Sites.Read.All (or Sites.Selected).
    sharepoint_tenant_id: str | None = None
    sharepoint_client_id: str | None = None
    sharepoint_client_secret: str | None = None
    # Google Drive, through a service account the folders are shared with
    # (read-only scope): the path to its JSON key, or the JSON itself.
    google_service_account_file: str | None = None
    google_service_account_json: str | None = None
    # Or, instead of service credentials, a reviewer signs in with Google or
    # Microsoft (OAuth) in the Repositories tab and picks the folders; the
    # sync then uses that account's access (app/sync/oauth.py). These are the
    # OAuth apps' client credentials; redirect URIs to register:
    #   {OAUTH_REDIRECT_BASE_URL}/oauth/google/callback
    #   {OAUTH_REDIRECT_BASE_URL}/oauth/microsoft/callback
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    microsoft_oauth_client_id: str | None = None
    microsoft_oauth_client_secret: str | None = None
    # "organizations" (any work/school account), "common" (also personal
    # Microsoft accounts) or a tenant id.
    microsoft_oauth_tenant: str = "organizations"
    oauth_redirect_base_url: str = "http://localhost:8000"
    # Where the callback sends the browser back to.
    frontend_url: str = "http://localhost:5173"
    # Fernet key that encrypts the accounts' tokens at rest; it also signs the
    # OAuth state. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str | None = None

    # Comma-separated list of origins allowed to call the API (the frontend dev server, etc.)
    cors_allowed_origins_raw: str = "http://localhost:5173"

    # Directory the original uploaded files are persisted to (ID-HU-FE-002's
    # "open original file" action and the PDF viewer both need the real bytes,
    # not just the extracted text/cells). Mounted as a Docker volume.
    document_storage_dir: str = "/data/documents"

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins_raw.split(",") if o.strip()]

    @property
    def embedding_dimensions(self) -> int:
        return (
            self.openai_embedding_dimensions
            if self.embedding_provider == "openai"
            else self.local_embedding_dimensions
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()