"""LangGraph pipeline for ID-HU-BE-008 — grounded answer generation with citations.

triage -> (ambiguous?) -> clarify -> END
       -> generate -> ground_and_validate -> END

`generate` calls the OpenAI chat model (via LangChain), constrained by the
system prompt to only use the retrieved sources it's shown. `ground_and_validate`
then enforces the grounding guarantee as code, not just as a prompt instruction:
any statement the model returns without a citation to a real, retrieved source
is dropped before it ever reaches the analyst.

ID-HU-BE-016: `generate` first sets aside lower-tier sources that give the
requested figure for the same entity and period as a higher-tier one
(app/qa/source_hierarchy.py), so the answer comes from the highest tier.

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
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.escalation import EscalationSuggestion, resolve_topic, suggest_escalation
from app.figures.extraction import entity_key as _entity_key, period_key, single_currency, source_row_label
from app.qa.confidence import _figures, _is_material, assess_confidence, unverified_figures
from app.qa.llm import get_chat_model
from app.qa.prompts import (
    SYSTEM_PROMPT,
    TRIAGE_PROMPT,
    build_triage_prompt,
    build_user_prompt,
    label_chunks,
)
from app.qa.source_hierarchy import SetAside, set_aside_lower_tiers
from app.qa.schemas import (
    ConfidenceOut,
    GroundedAnswer,
    GroundedStatement,
    LLMGroundedAnswer,
    LLMMetricSource,
    LLMTriage,
    ResolvedCitation,
)
from app.qa.statement_rows import row_figures
from app.ratios.mapping import normalize_label
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
    # Statement rows whose label is exactly what the question asks for
    # (app/qa/statement_rows.py): an answer must agree with them.
    statement_rows: list[RetrievedChunk]
    # ID-HU-BE-016: lower-tier sources removed in favour of higher-tier ones.
    set_aside: list[SetAside]


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
        # Neutral when the pre-check failed: no clarification, no penalty for
        # "the figure isn't in the sources" (which triage didn't get to say).
        triage = LLMTriage(metric_in_sources=True)
    else:
        triage = reconcile_triage(triage, source_map)
    return {"source_map": source_map, "triage_result": triage}


# Words in a file name that say what kind of document it is, not whose.
_GENERIC_FILE_WORDS = {
    "eerr", "eeff", "esf", "balance", "general", "estado", "estados", "resultados", "resumen", "reporte",
    "report", "presupuesto", "nomina", "informe", "financial", "statement", "statements", "consolidated",
    "earnings", "release", "results", "annual", "quarterly", "filed", "form", "notes", "notas", "the", "and",
    "del", "las", "los", "usd", "cop", "eur", "draft", "final", "version", "copy", "copia",
    # Source-system and report-kind words (ID-HU-BE-016): "SAP_Andina_Q2" is Andina's.
    "erp", "epm", "sap", "export", "exporte", "management", "gerencial", "interno", "internal", "monthly",
    "mensual", "forecast", "pronostico", "official", "oficial", "audited",
}


def entity_from_filename(filename: str) -> str | None:
    """The company a file name names, if it does ("EERR_Andina_2024.xlsx" ->
    "Andina", "2026q2-alphabet-earnings-release.pdf" -> "Alphabet",
    "10Q-Q3-2026-as-filed.pdf" -> None)."""
    stem = filename.rsplit(".", 1)[0].lower()
    words = [w for w in re.findall(r"[a-záéíóúñ]+", stem) if len(w) > 2 and w not in _GENERIC_FILE_WORDS]
    return words[0].title() if words else None


def reconcile_triage(triage: LLMTriage, source_map: dict) -> LLMTriage:
    """Check triage's per-source facts against the sources themselves.

    - An entity the source neither mentions nor is named after (the model
      put Apple's 10-Q under "Andina") is replaced by the one its file name
      gives, or left unknown.
    - Sources that state the requested figure but the model left out (three
      "Total revenues" rows of another company) are added, with the entity
      their file name gives — so a question about "revenue" across several
      companies still asks which one."""
    sources = []
    for item in triage.metric_sources:
        chunk = source_map.get(item.source_id)
        if chunk is None:
            continue
        where = f"{chunk.text} {chunk.document_filename}".lower()
        key = _entity_key(item.entity or "")
        stated = bool(key) and key.split()[0] in where
        entity = item.entity if stated else entity_from_filename(chunk.document_filename)
        sources.append(item.model_copy(update={"entity": entity}))

    listed = {item.source_id for item in sources}
    words = [w for w in re.findall(r"[a-záéíóúñ]+", (triage.requested_metric or "").lower()) if len(w) > 3]
    if words:
        stem = words[0][:5]  # "revenue" also finds "revenues"
        for source_id, chunk in source_map.items():
            if source_id not in listed and stem in chunk.text.lower() and re.search(r"\d[\d.,]{2,}", chunk.text):
                sources.append(LLMMetricSource(
                    source_id=source_id, entity=entity_from_filename(chunk.document_filename), period=None,
                ))
    if not sources:
        return triage
    return triage.model_copy(update={"metric_sources": sources})


MAX_CLARIFICATION_OPTIONS = 6


# Legal-form suffixes ignored when telling entities apart ("Andina" = "Andina S.A.S.").
def _distinct(values: list[str], key=lambda v: " ".join(v.lower().split())) -> list[str]:
    """Distinct values, keeping the first spelling of each (the longest when
    one is a fuller form of another, e.g. "Andina S.A.S." over "Andina")."""
    seen: dict[str, str] = {}
    for value in values:
        if not value or not value.strip():
            continue
        k = key(value)
        if k and (k not in seen or len(value.strip()) > len(seen[k])):
            seen[k] = value.strip()
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
    by_source = triage.metric_sources
    entities = _distinct(
        triage.entities_with_metric + [m.entity for m in by_source if m.entity], key=_entity_key
    )
    periods = _distinct(triage.periods_with_metric + [m.period for m in by_source if m.period])
    missing_entity = len(entities) > 1 and not triage.question_specifies_entity
    missing_period = len(periods) > 1 and not triage.question_specifies_period

    if missing_entity and missing_period:
        question = "Which entity and period do you mean?"
        # Only the combinations the sources actually have (no "Pacífico – 2025"
        # when there's no such figure); every pairing when that isn't known.
        canonical = {_entity_key(e): e for e in entities}
        observed = _distinct([
            f"{canonical.get(_entity_key(m.entity), m.entity)} – {m.period}"
            for m in by_source if m.entity and m.period
        ])
        # A company whose sources don't say the period is offered by name alone.
        dated = {_entity_key(m.entity) for m in by_source if m.entity and m.period}
        undated = _distinct([
            canonical.get(_entity_key(m.entity), m.entity)
            for m in by_source if m.entity and _entity_key(m.entity) not in dated
        ], key=_entity_key)
        options = (observed + undated) or [f"{entity} – {period}" for entity in entities for period in periods]
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
        return {"source_map": {}, "raw_answer": LLMGroundedAnswer(statements=[], unsupported=True), "set_aside": []}

    source_map = state.get("source_map") or label_chunks(chunks)
    # ID-HU-BE-016: the same figure from a higher tier wins.
    source_map, set_aside = set_aside_lower_tiers(state.get("triage_result") or LLMTriage(), source_map)
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
    return {"source_map": source_map, "raw_answer": raw_answer, "set_aside": set_aside}


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


_PLANNED = re.compile(
    r"presupuest|budget|forecast|proyecci|projection|projected|pron[oó]stico", re.IGNORECASE
)


def _figure_kind(statement: GroundedStatement) -> str:
    """"planned" if it cites a budget/forecast source, else "actual"."""
    for citation in statement.citations:
        where = " ".join(filter(None, [citation.document_filename, citation.sheet_name, citation.text]))
        if _PLANNED.search(where):
            return "planned"
    return "actual"


def _statement_currency(statement: GroundedStatement) -> str | None:
    """The currency a statement's figure is in: as it says, else as its sources
    state. Amounts in different currencies (1,205 USD vs 4,820,000 COP) are
    not conflicting values of one fact."""
    return single_currency(statement.text) or single_currency(
        " ".join(c.text for c in statement.citations)
    )


def settle_conflicts(statements: list[GroundedStatement]) -> None:
    """Keep the model's "conflicting" flag only where it holds up: a conflict
    takes at least two statements of the same kind of figure. A budgeted
    figure differing from the actual one is expected, not a disagreement
    between sources, and a lone "conflicting" statement has nothing to
    conflict with."""
    flagged: dict[tuple, list[GroundedStatement]] = {}
    for statement in statements:
        if statement.conflicting:
            key = (_figure_kind(statement), _statement_currency(statement))
            flagged.setdefault(key, []).append(statement)
    for group in flagged.values():
        # Values of different lines (a total and its parts, a segment and the
        # consolidated figure) don't disagree.
        disagree = any(same_line(a, b) for i, a in enumerate(group) for b in group[i + 1:])
        if len(group) < 2 or not disagree:
            for statement in group:
                statement.conflicting = False


def _lines(statement: GroundedStatement) -> set[tuple]:
    """(document, table title, line label) of each table row the statement
    cites; text sources have no line and are left out."""
    lines = set()
    for c in statement.citations:
        label = line_label(c.text)
        if label:
            title = c.text.split("row '", 1)[0] if "row '" in c.text else None
            lines.add((c.document_id, title, label))
    return lines


def same_line(a: GroundedStatement, b: GroundedStatement) -> bool:
    """Whether two statements report the same line item, so different
    values are a real disagreement: the same label in two documents (BI
    report vs income statement), or in the same table of one document; not
    the same label in another table of the same document (a segment table's
    "Operating income" vs the consolidated one). Unknown (text) counts as same."""
    lines_a, lines_b = _lines(a), _lines(b)
    if not lines_a or not lines_b:
        return True
    return any(
        la == lb and (da != db or ta == tb)
        for da, ta, la in lines_a for db, tb, lb in lines_b
    )


def statement_row_mismatch(statements: list[GroundedStatement], rows: list[RetrievedChunk]) -> str | None:
    """Why the answer can't be trusted when a statement row named exactly
    by the question (in a document the answer cites) gives none of the
    answer's figures: the answer took the figure from somewhere else (a
    note's "Total", another line), not from the line asked about."""
    cited = {c.document_id for s in statements for c in s.citations}
    rows = [row for row in rows if row.document_id in cited]
    # Every reading of each figure: "60,801 million" is 60,801 as the table writes it.
    given = {
        value for s in statements for token, values in _figures(s.text) if _is_material(token) for value in values
    }
    if not rows or not given:
        return None
    for row in rows:
        if any(abs(a - b) <= 0.005 * max(abs(a), abs(b)) for a in given for b in row_figures(row)):
            return None
    row = rows[0]
    label = source_row_label(row.text) or "the line asked about"
    where = f"{row.document_filename}, page {row.page_number}" if row.page_number else row.document_filename
    return f"The statement line '{label}' ({where}) reports different figures than this answer; check it."


def _statement_figures(statement: GroundedStatement) -> frozenset[float]:
    return frozenset(
        max(values) for token, values in _figures(statement.text) if _is_material(token) and values
    )


_QUARTER_YEAR = re.compile(r"\b(?:q([1-4])|([1-4])q|([1-4])t)\s*(?:of\s+|de\s+|del\s+)?((?:19|20)\d{2})\b", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _statement_period(statement: GroundedStatement, triage_periods: set[str]) -> str | None:
    """The period a statement's figure is for. What the statement itself says
    comes first ("revenue for Q2 2026 / for 2025"): triage gives one period
    per source, which is wrong for a table row holding several years. Without
    one, triage's period is used only if every cited source mentions a
    single year; otherwise the period is unknown and no conflict is inferred."""
    quarters = {f"q{next(g for g in m.groups()[:3] if g)} {m.group(4)}" for m in _QUARTER_YEAR.finditer(statement.text)}
    if len(quarters) == 1:
        return quarters.pop()
    years = set(_YEAR.findall(statement.text))
    if len(years) == 1:
        return years.pop()
    if years or len(triage_periods) != 1:
        return None
    if any(len(set(_YEAR.findall(c.text))) > 1 for c in statement.citations):
        return None
    return period_key(next(iter(triage_periods)))


_EXCEL_ROW = re.compile(r"^Sheet '[^']*'(?: \((?:[^()]|\([^()]*\))*\))?, (.+?): ")


def line_label(source_text: str) -> str | None:
    """The line item a table-row source holds (PDF "row 'Net income'", or an
    Excel row "Sheet 'X' (…), Utilidad neta: …"), normalized; None for text."""
    label = source_row_label(source_text)
    if label is None:
        match = _EXCEL_ROW.match(source_text)
        label = match.group(1) if match else None
    return normalize_label(label.rsplit(":", 1)[-1]) or None if label else None


def detect_conflicts(statements: list[GroundedStatement], triage: LLMTriage, source_map: dict) -> None:
    """Flag disagreements the model didn't mark, using triage's per-source
    facts: statements citing the requested figure for the same entity,
    period and kind of figure (actual vs planned) that state different
    amounts are conflicting values of one fact."""
    facts = {m.source_id: m for m in triage.metric_sources if m.entity and m.period}
    groups: dict[tuple, list[GroundedStatement]] = {}
    for statement in statements:
        cited = [facts[c.source_id] for c in statement.citations if c.source_id in facts]
        entities = {_entity_key(m.entity) for m in cited}
        period = _statement_period(statement, {m.period for m in cited})
        figures = _statement_figures(statement)
        if len(entities) != 1 or period is None or not figures:
            continue
        key = (entities.pop(), period, _figure_kind(statement), _statement_currency(statement))
        groups.setdefault(key, []).append(statement)
    for group in groups.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                # Only values of the same line disagree (see same_line).
                if same_line(a, b) and not _all_close({_statement_figures(a), _statement_figures(b)}):
                    a.conflicting = b.conflicting = True


def _all_close(amounts: set[frozenset[float]]) -> bool:
    """Same amount up to rounding, with the reconciliation tolerance (0.1%)
    rather than figure verification's looser one: 4,795,000 vs 4,820,000
    (0.5%) is a real disagreement."""
    tolerance = get_settings().comparison_tolerance
    values = [max(a) for a in amounts]
    return all(abs(v - values[0]) <= tolerance * max(abs(values[0]), 1.0) for v in values)


_SOURCE_IDS = re.compile(r"\s*\[?\((?:S\d+(?:,\s*)?)+\)\]?|\s*\[(?:S\d+(?:,\s*)?)+\]")
_TRAILING_DEBRIS = re.compile(r"[\s\[\]{},]+$")


def clean_statement_text(text: str) -> str:
    """Drop source ids and structured-output debris the model sometimes
    leaves in a statement ("... 4,820,000 COP (S2).},{"); citations are
    shown separately."""
    cleaned = _TRAILING_DEBRIS.sub("", _SOURCE_IDS.sub("", text)).strip()
    return cleaned or text


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
        statement = GroundedStatement(
            text=clean_statement_text(stmt.text), citations=resolved, conflicting=stmt.conflicting
        )
        # ID-HU-BE-009: ...and every figure it states must appear in those sources.
        missing = unverified_figures(statement)
        if missing:
            removed_figures.extend(missing)
            continue
        statements.append(statement)
    settle_conflicts(statements)
    detect_conflicts(statements, triage, source_map)

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
            has_conflicts=any(s.conflicting for s in statements),
            metric_missing=not triage.metric_in_sources,
        )
        if any(s.conflicting for s in statements):
            # Same as an unreconciled comparison (ID-HU-FE-003): consolidation owns it.
            escalation = suggest_escalation(
                "consolidation", "The sources report different values for the same figure."
            )
        mismatch = statement_row_mismatch(statements, state.get("statement_rows") or [])
        if mismatch:
            confidence = ConfidenceOut(
                level="low", score=min(confidence.score, 0.4), reasons=[*confidence.reasons, mismatch]
            )
        if escalation is None and confidence.level == "low":
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
