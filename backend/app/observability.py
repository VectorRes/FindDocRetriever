"""Tracing of the agent with Langfuse, for debugging (self-hosted, see README).

Each entry point is one trace — a chat question (`qa.answer`), a ratio
calculation, a dashboard refresh, a comparison — with the user, the chat
session and its input and output. Inside it, the LangGraph nodes (triage,
generate, ground_and_validate) and every chat-model call (prompt, response,
tokens, latency) are nested, through the LangChain callback handler.
Requests that fan out to worker threads (ratio extraction) stay in their
trace: the workers run in a copy of the request's context.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (and
LANGFUSE_TRACING_ENABLED isn't false): then every function here is a no-op,
so tracing never changes what the app does. Spans are exported in the
background; an unreachable Langfuse only logs a warning.
"""
import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from app.config import get_settings

logger = logging.getLogger(__name__)


class _NoSpan:
    """Stands in for a Langfuse observation when tracing is off."""

    def update(self, **_: Any) -> "_NoSpan":
        return self


@lru_cache
def _client():
    """The Langfuse client, or None when tracing is off. Created once; it
    registers itself, so the LangChain handler and get_client() find it."""
    settings = get_settings()
    if not (settings.langfuse_tracing_enabled and settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_environment,
        )
    except Exception:  # noqa: BLE001 — tracing must never break the app
        logger.warning("Langfuse tracing couldn't start; continuing without it", exc_info=True)
        return None


def tracing_enabled() -> bool:
    return _client() is not None


@lru_cache
def _handler():
    from langfuse.langchain import CallbackHandler

    return CallbackHandler(public_key=get_settings().langfuse_public_key)


def langchain_callbacks() -> list:
    """Callbacks for LangChain / LangGraph runs: one shared handler, so a
    model that has it and a graph run that passes it don't record a call twice."""
    return [_handler()] if tracing_enabled() else []


@contextmanager
def trace(
    name: str,
    *,
    input: Any = None,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[Any]:
    """A span for one unit of work; at the top of a request it is the trace,
    named `name`, with `user_id` and `session_id` on it and on everything
    nested inside. Yields the span: call `.update(output=...)` on it."""
    client = _client()
    if client is None:
        yield _NoSpan()
        return
    from langfuse import propagate_attributes

    with client.start_as_current_observation(name=name, as_type="agent", input=input, metadata=metadata) as span:
        with propagate_attributes(
            user_id=user_id or None, session_id=session_id or None, trace_name=name, tags=tags,
        ):
            yield span


@contextmanager
def span(name: str, *, input: Any = None, as_type: str = "span") -> Iterator[Any]:
    """A step inside the current trace (retrieval, a calculation); a no-op
    when tracing is off. Yields the span: call `.update(output=...)` on it."""
    client = _client()
    if client is None:
        yield _NoSpan()
        return
    with client.start_as_current_observation(name=name, as_type=as_type, input=input) as observation:
        yield observation


def flush() -> None:
    """Send what's buffered (on shutdown; spans are otherwise sent in batches)."""
    client = _client()
    if client is not None:
        client.flush()
