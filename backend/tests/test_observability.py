"""Langfuse tracing of the agent (app/observability.py).

No Langfuse server: the client exports to an in-memory span exporter, so the
tests see exactly the spans that would be sent. The chat model is scripted
as a real LangChain chat model, so its calls go through the callback handler
like real ones.
"""
import uuid

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.observability as observability
from app.qa.graph import get_qa_graph
from app.retrieval.service import RetrievedChunk


@pytest.fixture
def no_tracing(monkeypatch):
    monkeypatch.setattr(observability, "_client", lambda: None)


def test_everything_is_a_no_op_when_tracing_is_off(no_tracing):
    assert not observability.tracing_enabled()
    assert observability.langchain_callbacks() == []
    with observability.trace("qa.answer", input="q", user_id="u", session_id="s") as traced:
        assert traced.update(output="a") is traced
    with observability.span("retrieval") as step:
        step.update(output=[])
    observability.flush()


def test_tracing_is_off_without_keys(monkeypatch):
    from app.config import Settings

    observability._client.cache_clear()
    monkeypatch.setattr(observability, "get_settings", lambda: Settings(
        langfuse_public_key=None, langfuse_secret_key=None, langfuse_tracing_enabled=True,
    ))
    try:
        assert observability._client() is None
    finally:
        observability._client.cache_clear()


@pytest.fixture
def exported(monkeypatch):
    """A real Langfuse client whose spans land in memory."""
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    # conftest turns tracing off for the suite; the SDK reads the variable too.
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    exporter = InMemorySpanExporter()
    # Langfuse keeps one client per public key: a new key per test.
    public_key = f"pk-lf-test-{uuid.uuid4().hex[:8]}"
    client = Langfuse(
        public_key=public_key, secret_key="sk-lf-test", base_url="http://localhost:1",
        span_exporter=exporter, flush_at=1,
    )
    handler = CallbackHandler(public_key=public_key)
    monkeypatch.setattr(observability, "_client", lambda: client)
    monkeypatch.setattr(observability, "_handler", lambda: handler)

    def spans():
        client.flush()
        return exporter.get_finished_spans()

    yield spans
    client.shutdown()


def _attributes(span) -> dict:
    return dict(span.attributes or {})


def test_a_trace_carries_the_user_and_session_down_to_nested_spans(exported):
    with observability.trace("qa.answer", input={"question": "q"}, user_id="ana", session_id="s-1") as traced:
        with observability.span("retrieval", input={"question": "q"}) as step:
            step.update(output={"sources": []})
        traced.update(output={"grounded": True})

    by_name = {s.name: s for s in exported()}
    assert {"qa.answer", "retrieval"} <= set(by_name)
    root, child = by_name["qa.answer"], by_name["retrieval"]
    assert child.parent.span_id == root.context.span_id
    for span in (root, child):
        values = set(_attributes(span).values())
        assert "ana" in values and "s-1" in values


class _ScriptedChatModel(GenericFakeChatModel):
    """A LangChain chat model whose structured output is scripted."""

    def with_structured_output(self, schema, **_):
        from langchain_core.runnables import RunnableLambda

        model = self

        def answer(messages):
            model.invoke(messages)  # a traced model call, like the real one
            return schema.model_validate(model.scripted[schema.__name__])

        return RunnableLambda(answer)


def test_the_qa_graph_nodes_and_model_calls_are_nested_in_the_trace(exported, monkeypatch):
    import app.qa.graph as qa_graph

    model = _ScriptedChatModel(messages=iter([AIMessage(content="ok")] * 4), callbacks=observability.langchain_callbacks())
    object.__setattr__(model, "scripted", {
        "LLMTriage": {"requested_metric": "net income", "metric_in_sources": True},
        "LLMGroundedAnswer": {"statements": [{"text": "Net income was 120,000.", "citations": [{"source_id": "S1"}]}]},
    })
    monkeypatch.setattr(qa_graph, "get_chat_model", lambda: model)
    chunk = RetrievedChunk(document_id="d", document_filename="eerr.xlsx", sheet_name="EERR", cell_range="A2:B2",
                           page_number=None, text="Utilidad neta: 2024=120000")

    with observability.trace("qa.answer", user_id="ana", session_id="s-1"):
        result = get_qa_graph().invoke(
            {"question": "Net income 2024?", "chunks": [chunk]},
            config={"callbacks": observability.langchain_callbacks(), "run_name": "qa_graph"},
        )

    assert result["answer"].grounded
    spans = exported()
    names = [s.name for s in spans]
    assert {"qa.answer", "qa_graph", "triage", "generate", "ground_and_validate"} <= set(names)
    generations = [s for s in spans if _attributes(s).get("langfuse.observation.type") == "generation"]
    assert len(generations) >= 2  # triage + answer
    root = next(s for s in spans if s.name == "qa.answer")
    assert all(s.context.trace_id == root.context.trace_id for s in spans)
