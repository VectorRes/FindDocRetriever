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

    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    local_embedding_dimensions: int = 384

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
