from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import get_settings


@lru_cache
def get_chat_model() -> ChatOpenAI:
    """Chat model used as the generation agent, authenticated with the OpenAI API key.

    Cached so the underlying client is created once; tests monkeypatch this
    function directly rather than reaching through to a real OpenAI call.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for the QA generation agent")

    return ChatOpenAI(
        model=settings.openai_chat_model,
        api_key=settings.openai_api_key,
        temperature=settings.qa_temperature,
    )
