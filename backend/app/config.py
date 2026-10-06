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
    # ID-HU-FE-003: relative difference below which compared values count as
    # reconciling (0.001 = 0.1%, absorbs rounding but flags a 0.5% gap).
    comparison_tolerance: float = 0.001

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