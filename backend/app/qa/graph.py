"""LangGraph pipeline for ID-HU-BE-008 — grounded answer generation with citations.

generate -> ground_and_validate -> END

`generate` calls the OpenAI chat model (via LangChain), constrained by the
system prompt to only use the retrieved sources it's shown. `ground_and_validate`
then enforces the grounding guarantee as code, not just as a prompt instruction:
any statement the model returns without a citation to a real, retrieved source
is dropped before it ever reaches the analyst.
"""
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.qa.llm import get_chat_model
from app.qa.prompts import SYSTEM_PROMPT, build_user_prompt, label_chunks
from app.qa.schemas import (
    GroundedAnswer,
    GroundedStatement,
    LLMGroundedAnswer,
    ResolvedCitation,
)
from app.retrieval.service import RetrievedChunk


class QAState(TypedDict, total=False):
    question: str
    chunks: list[RetrievedChunk]
    # Prior (question, answer_text) turns from the same conversation session,
    # oldest first — context only, never a citable source (ID-HU-FE-001).
    history: list[tuple[str, str]]
    source_map: dict[str, RetrievedChunk]
    raw_answer: LLMGroundedAnswer
    answer: GroundedAnswer
    restriction_notice: str | None


def generate_node(state: QAState) -> QAState:
    chunks = state.get("chunks") or []
    if not chunks:
        # Nothing retrieved: don't even call the model, there is nothing to ground on.
        return {"source_map": {}, "raw_answer": LLMGroundedAnswer(statements=[], unsupported=True)}

    source_map = label_chunks(chunks)
    structured_llm = get_chat_model().with_structured_output(LLMGroundedAnswer)
    raw_answer = structured_llm.invoke(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(state["question"], source_map, state.get("history")),
            },
        ]
    )
    return {"source_map": source_map, "raw_answer": raw_answer}


def _resolve_citation(source_id: str, source_map: dict[str, RetrievedChunk]) -> ResolvedCitation:
    chunk = source_map[source_id]
    return ResolvedCitation(
        source_id=source_id,
        document_id=chunk.document_id,
        document_filename=chunk.document_filename,
        sheet_name=chunk.sheet_name,
        cell_range=chunk.cell_range,
        page_number=chunk.page_number,
        reference_number=chunk.reference_number,
        text=chunk.text,
    )


def ground_and_validate_node(state: QAState) -> QAState:
    raw = state.get("raw_answer") or LLMGroundedAnswer(statements=[], unsupported=True)
    source_map = state.get("source_map", {})

    statements: list[GroundedStatement] = []
    for stmt in raw.statements:
        # Enforcement, not just prompting: a statement only survives if it cites
        # at least one source id that was actually retrieved. This is what
        # guarantees "every factual statement... backed by at least one citation".
        resolved = [
            _resolve_citation(citation.source_id, source_map)
            for citation in stmt.citations
            if citation.source_id in source_map
        ]
        if not resolved:
            continue
        statements.append(
            GroundedStatement(text=stmt.text, citations=resolved, conflicting=stmt.conflicting)
        )

    notice = state.get("restriction_notice")
    if notice:
        # A notice is intentionally not a factual document statement, so it
        # does not require a source citation.
        statements.append(GroundedStatement(text=notice, notice=True))

    answer = GroundedAnswer(
        question=state["question"],
        statements=statements,
        sources=[_resolve_citation(sid, source_map) for sid in source_map],
        has_conflicts=any(s.conflicting for s in statements),
        grounded=(bool(statements) and not raw.unsupported) or bool(notice),
        restriction_notice=notice,
    )
    return {"answer": answer}


def build_qa_graph():
    graph = StateGraph(QAState)
    graph.add_node("generate", generate_node)
    graph.add_node("ground_and_validate", ground_and_validate_node)
    graph.set_entry_point("generate")
    graph.add_edge("generate", "ground_and_validate")
    graph.add_edge("ground_and_validate", END)
    return graph.compile()


_compiled_graph = None


def get_qa_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_qa_graph()
    return _compiled_graph
