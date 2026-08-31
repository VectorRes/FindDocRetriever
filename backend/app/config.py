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

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    retrieval_top_k: int = 8

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