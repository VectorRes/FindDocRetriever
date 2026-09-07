"""Unit tests for ID-HU-BE-008 — grounded answer generation with citations.

These tests never call the real OpenAI API: `get_chat_model` is monkeypatched
with a fake chat model that returns a pre-scripted LLMGroundedAnswer, the same
way conftest.py fakes the embedding provider for retrieval tests. This lets us
test the LangGraph pipeline's *enforcement* logic (dropping ungrounded
statements, resolving citations, flagging conflicts) deterministically.
"""
import app.qa.graph as qa_graph_module
from app.qa.graph import get_qa_graph
from app.qa.schemas import LLMCitation, LLMGroundedAnswer, LLMStatement
from app.retrieval.service import RetrievedChunk


def make_chunk(**overrides) -> RetrievedChunk:
    defaults = dict(
        document_id="doc-1",
        document_filename="income_statement.pdf",
        sheet_name=None,
        cell_range=None,
        page_number=4,
        text="Net income for Q1 was $120,000.",
        content_type="text",
        reference_number=None,
        extraction_method="native",
        confidence=None,
        confidence_label=None,
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


class _FakeStructuredLLM:
    def __init__(self, response: LLMGroundedAnswer):
        self._response = response

    def invoke(self, messages):
        return self._response


class _FakeChatModel:
    def __init__(self, response: LLMGroundedAnswer):
        self._response = response

    def with_structured_output(self, schema):
        return _FakeStructuredLLM(self._response)


def _patch_llm(monkeypatch, response: LLMGroundedAnswer) -> None:
    monkeypatch.setattr(qa_graph_module, "get_chat_model", lambda: _FakeChatModel(response))


def test_every_returned_statement_has_at_least_one_citation(monkeypatch):
    chunks = [make_chunk()]
    raw = LLMGroundedAnswer(
        statements=[
            LLMStatement(text="Net income was $120,000.", citations=[LLMCitation(source_id="S1")]),
            # No citation at all -> must be dropped.
            LLMStatement(text="Made-up unsupported claim.", citations=[]),
            # Cites a source id that was never given to the model -> must be dropped.
            LLMStatement(text="Claims a source that doesn't exist.", citations=[LLMCitation(source_id="S99")]),
        ]
    )
    _patch_llm(monkeypatch, raw)

    result = get_qa_graph().invoke({"question": "What was net income in Q1?", "chunks": chunks})
    answer = result["answer"]

    assert len(answer.statements) == 1
    assert all(len(statement.citations) >= 1 for statement in answer.statements)
    assert answer.statements[0].citations[0].document_filename == "income_statement.pdf"
    assert answer.grounded is True


def test_multi_document_answer_cites_all_contributing_sources(monkeypatch):
    chunks = [
        make_chunk(
            document_id="doc-income",
            document_filename="income_statement.pdf",
            text="Net income was $120,000.",
        ),
        make_chunk(
            document_id="doc-cashflow",
            document_filename="cash_flow_statement.pdf",
            page_number=2,
            text="The cash flow statement starts from net income of $120,000.",
        ),
    ]
    raw = LLMGroundedAnswer(
        statements=[
            LLMStatement(
                text="Net income reconciles with the cash flow statement's starting point.",
                citations=[LLMCitation(source_id="S1"), LLMCitation(source_id="S2")],
            )
        ]
    )
    _patch_llm(monkeypatch, raw)

    result = get_qa_graph().invoke(
        {"question": "Does net income reconcile with the cash flow statement?", "chunks": chunks}
    )
    answer = result["answer"]

    cited_docs = {citation.document_filename for s in answer.statements for citation in s.citations}
    assert cited_docs == {"income_statement.pdf", "cash_flow_statement.pdf"}


def test_conflicting_sources_are_both_presented_with_their_own_citations(monkeypatch):
    chunks = [
        make_chunk(
            document_id="doc-bi",
            document_filename="bi_export.xlsx",
            sheet_name="Revenue",
            cell_range="B2",
            page_number=None,
            text="Q2 revenue: 4,820,000",
        ),
        make_chunk(
            document_id="doc-erp",
            document_filename="erp_report.pdf",
            sheet_name=None,
            cell_range=None,
            page_number=1,
            text="Q2 revenue: 4,795,000",
        ),
    ]
    raw = LLMGroundedAnswer(
        statements=[
            LLMStatement(
                text="The BI export reports Q2 revenue of 4,820,000.",
                citations=[LLMCitation(source_id="S1")],
                conflicting=True,
            ),
            LLMStatement(
                text="The ERP report reports Q2 revenue of 4,795,000.",
                citations=[LLMCitation(source_id="S2")],
                conflicting=True,
            ),
        ]
    )
    _patch_llm(monkeypatch, raw)

    result = get_qa_graph().invoke({"question": "What was Q2 revenue?", "chunks": chunks})
    answer = result["answer"]

    assert answer.has_conflicts is True
    assert len(answer.statements) == 2
    cited_docs = {s.citations[0].document_filename for s in answer.statements}
    assert cited_docs == {"bi_export.xlsx", "erp_report.pdf"}
    # Each conflicting value keeps its own, separate citation rather than one
    # value being silently chosen or merged with the other's source.
    for statement in answer.statements:
        assert len(statement.citations) == 1
        assert statement.conflicting is True


def test_no_retrieved_sources_short_circuits_without_calling_the_model(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("the generation agent should not be called with zero sources")

    monkeypatch.setattr(qa_graph_module, "get_chat_model", _fail_if_called)

    result = get_qa_graph().invoke(
        {"question": "What is our supplier concentration risk?", "chunks": []}
    )
    answer = result["answer"]

    assert answer.grounded is False
    assert answer.statements == []
    assert answer.sources == []
