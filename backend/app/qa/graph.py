"""LangGraph pipeline for ID-HU-BE-008 — grounded answer generation with citations.

triage -> (ambiguous?) -> clarify -> END
       -> generate -> ground_and_validate -> END

`generate` calls the OpenAI chat model (via LangChain), constrained by the
system prompt to only use the retrieved sources it's shown. `ground_and_validate`
then enforces the grounding guarantee as code, not just as a prompt instruction:
any statement the model returns without a citation to a real, retrieved source
is dropped before it ever reaches the analyst.

ID-HU-BE-009 adds:
  - `triage`, a short focused LLM call before generation that decides whether
    the question is ambiguous given the retrieved sources (missing entity
    and/or period) and whether it asks for a professional judgment. Ambiguous
    questions go to `clarify` and get a clarification request, never a
    guessed answer.
  - in `ground_and_validate`: statements whose figures don't appear in their
    cited sources are dropped too, every answer gets a confidence assessment,
    and an escalation-team suggestion is attached when there's no confident
    answer.
"""
import logging
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.escalation import EscalationSuggestion, resolve_topic, suggest_escalation
from app.qa.confidence import assess_confidence, unverified_figures
from app.qa.llm import get_chat_model
from app.qa.prompts import (
    SYSTEM_PROMPT,
    TRIAGE_PROMPT,
    build_triage_prompt,
    build_user_prompt,
    label_chunks,
)
from app.qa.schemas import (
    GroundedAnswer,
    GroundedStatement,
    LLMGroundedAnswer,
    LLMTriage,
    ResolvedCitation,
)
from app.retrieval.service import RetrievedChunk

logger = logging.getLogger(__name__)


class QAState(TypedDict, total=False):
    question: str
    chunks: list[RetrievedChunk]
    # Prior (question, answer_text) turns from the same conversation session,
    # oldest first — context only, never a citable source (ID-HU-FE-001).
    history: list[tuple[str, str]]
    source_map: dict[str, RetrievedChunk]
    raw_answer: LLMGroundedAnswer
    triage_result: LLMTriage
    answer: GroundedAnswer
    restriction_notice: str | None


def triage_node(state: QAState) -> QAState:
    chunks = state.get("chunks") or []
    source_map = label_chunks(chunks)
    if not chunks:
        return {"source_map": source_map, "triage_result": LLMTriage()}

    try:
        triage = get_chat_model().with_structured_output(LLMTriage).invoke(
            [
                {"role": "system", "content": TRIAGE_PROMPT},
                {
                    "role": "user",
                    "content": build_triage_prompt(state["question"], source_map, state.get("history")),
                },
            ]
        )
    except Exception:  # noqa: BLE001 — a failed pre-check must not block answering
        logger.warning("QA triage failed; answering without ambiguity/judgment checks", exc_info=True)
        triage = None
    if not isinstance(triage, LLMTriage):
        triage = LLMTriage()
    return {"source_map": source_map, "triage_result": triage}


MAX_CLARIFICATION_OPTIONS = 6


def _distinct(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        key = " ".join(value.lower().split())
        if key and key not in seen:
            seen[key] = value.strip()
    return list(seen.values())


def clarification_for(triage: LLMTriage) -> tuple[str, list[str]] | None:
    """ID-HU-BE-009: decide, in code, whether a question is ambiguous.

    It is when the sources hold the requested figure for more than one
    entity or more than one period and the question (with its history)
    doesn't say which. A metric the sources don't contain is never ambiguous:
    that's a "no confident answer" case. Returns the clarification question
    and the options to offer, or None."""
    if not triage.metric_in_sources:
        return None
    entities = _distinct(triage.entities_with_metric)
    periods = _distinct(triage.periods_with_metric)
    missing_entity = len(entities) > 1 and not triage.question_specifies_entity
    missing_period = len(periods) > 1 and not triage.question_specifies_period

    if missing_entity and missing_period:
        question = "Which entity and period do you mean?"
        options = [f"{entity} – {period}" for entity in entities for period in periods]
    elif missing_entity:
        question = "Which entity do you mean?"
        options = entities
    elif missing_period:
        question = "Which period do you mean?"
        options = periods
    else:
        return None
    return question, options[:MAX_CLARIFICATION_OPTIONS]


def _route_after_triage(state: QAState) -> str:
    triage = state.get("triage_result") or LLMTriage()
    if state.get("source_map") and clarification_for(triage):
        return "clarify"
    return "generate"


def clarify_node(state: QAState) -> QAState:
    """ID-HU-BE-009: ask which entity/period is meant instead of guessing."""
    question, options = clarification_for(state["triage_result"])
    source_map = state.get("source_map", {})
    return {"answer": GroundedAnswer(
        question=state["question"],
        sources=[_resolve_citation(sid, source_map) for sid in source_map],
        needs_clarification=True,
        clarification_question=question,
        clarification_options=options,
        restriction_notice=state.get("restriction_notice"),
    )}


def generate_node(state: QAState) -> QAState:
    chunks = state.get("chunks") or []
    if not chunks:
        # Nothing retrieved: don't even call the model, there is nothing to ground on.
        return {"source_map": {}, "raw_answer": LLMGroundedAnswer(statements=[], unsupported=True)}

    source_map = state.get("source_map") or label_chunks(chunks)
    structured_llm = get_chat_model().with_structured_output(LLMGroundedAnswer)
    raw_answer = structured_llm.invoke(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(
                    state["question"],
                    source_map,
                    state.get("history"),
                    requires_judgment=(state.get("triage_result") or LLMTriage()).requires_judgment,
                ),
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
    notice = state.get("restriction_notice")
    triage = state.get("triage_result") or LLMTriage()
    sources = [_resolve_citation(sid, source_map) for sid in source_map]

    statements: list[GroundedStatement] = []
    removed_figures: list[str] = []
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
        statement = GroundedStatement(text=stmt.text, citations=resolved, conflicting=stmt.conflicting)
        # ID-HU-BE-009: ...and every figure it states must appear in those sources.
        missing = unverified_figures(statement)
        if missing:
            removed_figures.extend(missing)
            continue
        statements.append(statement)

    answered = bool(statements) and not raw.unsupported
    topic = resolve_topic(triage.topic, state["question"])
    confidence = None
    escalation: EscalationSuggestion | None = None
    if answered:
        cited_ids = {c.source_id for s in statements for c in s.citations}
        cited_scores = [
            score for sid in cited_ids
            if (score := getattr(source_map[sid], "score", None)) is not None
        ]
        confidence = assess_confidence(
            kept_count=len(statements),
            proposed_count=len(raw.statements),
            removed_figures=removed_figures,
            self_confidence=raw.self_confidence,
            cited_scores=cited_scores,
            requires_judgment=triage.requires_judgment,
        )
        if confidence.level == "low":
            escalation = suggest_escalation(topic, "The system's confidence in this answer is low.")
    else:
        # No answer at all: point the analyst at the team that owns the topic.
        escalation = suggest_escalation(
            topic,
            "No answer could be given from the sources you can access."
            if notice else "No reliable source was found to answer this question.",
        )

    if notice:
        # A notice is intentionally not a factual document statement, so it
        # does not require a source citation.
        statements.append(GroundedStatement(text=notice, notice=True))

    answer = GroundedAnswer(
        question=state["question"],
        statements=statements,
        sources=sources,
        has_conflicts=any(s.conflicting for s in statements),
        grounded=answered or bool(notice),
        restriction_notice=notice,
        confidence=confidence,
        escalation=escalation,
    )
    return {"answer": answer}


def build_qa_graph():
    graph = StateGraph(QAState)
    graph.add_node("triage", triage_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("generate", generate_node)
    graph.add_node("ground_and_validate", ground_and_validate_node)
    graph.set_entry_point("triage")
    graph.add_conditional_edges(
        "triage", _route_after_triage, {"clarify": "clarify", "generate": "generate"}
    )
    graph.add_edge("clarify", END)
    graph.add_edge("generate", "ground_and_validate")
    graph.add_edge("ground_and_validate", END)
    return graph.compile()


_compiled_graph = None


def get_qa_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_qa_graph()
    return _compiled_graph
