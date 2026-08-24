from functools import lru_cache

from app.config import get_settings
from app.embeddings.base import EmbeddingProvider


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()

    if settings.embedding_provider == "openai":
        from app.embeddings.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dimensions,
        )

    if settings.embedding_provider == "local":
        from app.embeddings.local_provider import LocalEmbeddingProvider

        return LocalEmbeddingProvider(model_name=settings.local_embedding_model)

    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")
