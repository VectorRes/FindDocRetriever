from app.retrieval.service import RetrievedChunk

SYSTEM_PROMPT = """\
You are the answer-generation engine for FinDoc Retriever, a financial \
document assistant used by financial analysts.

You will be given a QUESTION and a numbered list of SOURCES, each one an \
excerpt retrieved from the analyst's financial documents (PDF pages or \
Excel cells). Follow these rules strictly:

1. Use ONLY the information in SOURCES. Never use outside knowledge, and \
   never invent, estimate, or infer a figure that is not explicitly present \
   in a source.
2. Break your answer into short, independent factual statements. Each \
   statement must contain exactly one claim or figure.
3. Every statement MUST cite at least one source, by its exact id (e.g. \
   "S1"). Never reference a source id that was not given to you. A \
   statement you cannot support with a real source id is not allowed — \
   omit it instead.
4. If a statement is supported by more than one source (e.g. the same \
   figure appears consistently in two documents), cite all of them.
5. If two or more sources disagree about the same fact (e.g. two documents \
   report different values for the same period/metric), do NOT silently \
   pick one or average them. Instead, output one statement per conflicting \
   value, each citing only the source(s) for that value, and mark each of \
   those statements as conflicting.
6. If the sources do not contain enough information to answer the question \
   at all, set unsupported=true and leave statements empty rather than \
   guessing.
7. You may be given CONVERSATION HISTORY from earlier turns. Use it only to \
   interpret the current QUESTION — e.g. resolving "it", "that", or an \
   implicit follow-up like "and last quarter?". Never treat a fact stated in \
   the history as a citable source: every statement must still be grounded \
   in SOURCES.
8. Set self_confidence honestly: "high" only when SOURCES state the answer \
   explicitly; "low" when they only partially or indirectly address it, or \
   when the question asks for a judgment (such as how a contract's revenue \
   should be recognised) that the SOURCES do not state outright.
9. Never state a rule, policy or accounting treatment from general \
   knowledge as if a source said it. Only state what the SOURCES say.
"""

# ID-HU-BE-009: run before generation, as its own focused task.
TRIAGE_PROMPT = """\
You triage questions for FinDoc Retriever, a financial document assistant, \
BEFORE any answer is written. You are given a QUESTION, optional \
CONVERSATION HISTORY, and a short list of the SOURCES retrieved for it. \
Do not answer the question. Report only these facts:

1. metric_in_sources: do the SOURCES contain the figure or fact the \
   QUESTION asks for? If not, leave the entity and period lists empty.
2. entities_with_metric: every distinct company/subsidiary/entity for \
   which the SOURCES give that figure (use the names in the sources or \
   their file names).
3. periods_with_metric: every distinct period (year, quarter, month, date) \
   for which the SOURCES give that figure. A table row such as \
   "Revenue: Q3 2024=850,000; Q2 2024=790,000" gives two periods.
4. question_specifies_entity / question_specifies_period: does the \
   QUESTION, read with the CONVERSATION HISTORY, already say which entity \
   / which period it means? A reply to an earlier clarification (e.g. \
   "Andina 2024") counts as specifying it.
5. requires_judgment: does answering need a professional judgment or \
   recommendation (how revenue should be recognised, which accounting \
   treatment applies, whether something complies with a rule) rather than \
   reporting what the documents state?
6. topic: the finance area of the question.
"""


def label_chunks(chunks: list[RetrievedChunk]) -> dict[str, RetrievedChunk]:
    return {f"S{i + 1}": chunk for i, chunk in enumerate(chunks)}


def _describe_location(chunk: RetrievedChunk) -> str:
    parts = [chunk.document_filename]
    if chunk.sheet_name:
        parts.append(f"sheet '{chunk.sheet_name}'")
    if chunk.cell_range:
        parts.append(f"cell(s) {chunk.cell_range}")
    if chunk.page_number is not None:
        parts.append(f"page {chunk.page_number}")
    if chunk.reference_number:
        parts.append(f"note/ref {chunk.reference_number}")
    return ", ".join(parts)


def build_triage_prompt(
    question: str,
    source_map: dict[str, RetrievedChunk],
    history: list[tuple[str, str]] | None = None,
) -> str:
    lines: list[str] = []
    if history:
        lines.append("CONVERSATION HISTORY:")
        for prior_question, prior_answer in history:
            lines.append(f"Q: {prior_question}")
            lines.append(f"A: {prior_answer}")
        lines.append("")
    lines += [f"QUESTION: {question}", "", "SOURCES:"]
    for source_id, chunk in source_map.items():
        excerpt = " ".join(chunk.text.split())[:220]
        lines.append(f"[{source_id}] ({_describe_location(chunk)}) {excerpt}")
    return "\n".join(lines)


def build_user_prompt(
    question: str,
    source_map: dict[str, RetrievedChunk],
    history: list[tuple[str, str]] | None = None,
    requires_judgment: bool = False,
) -> str:
    lines: list[str] = []
    if history:
        lines.append("CONVERSATION HISTORY (context only — do not cite as a source):")
        for prior_question, prior_answer in history:
            lines.append(f"Q: {prior_question}")
            lines.append(f"A: {prior_answer}")
        lines.append("")
    lines += [f"QUESTION: {question}", ""]
    if requires_judgment:
        # ID-HU-BE-009: triage found this asks for a professional judgment.
        lines += [
            "NOTE: This question asks for a professional judgment. Report only the relevant "
            "facts the SOURCES state; do not state a conclusion, rule or treatment yourself.",
            "",
        ]
    lines.append("SOURCES:")
    for source_id, chunk in source_map.items():
        lines.append(f"[{source_id}] ({_describe_location(chunk)})")
        lines.append(chunk.text.strip())
        lines.append("")
    return "\n".join(lines)
