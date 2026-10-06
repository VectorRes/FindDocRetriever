"""Data contracts for the QA generation agent (ID-HU-BE-008).

Two layers on purpose:
  - LLM* models: the structured-output contract the OpenAI chat model must
    fill in. It only knows sources by their short label (e.g. "S1"), never
    the underlying document metadata, so it cannot fabricate a citation to
    something that wasn't actually retrieved.
  - Resolved*/Grounded*: the app-facing result, where every citation has
    been mapped back to real document/page/sheet/cell metadata and every
    statement that lacks a valid citation has already been dropped.
"""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.escalation import TOPICS, EscalationSuggestion

Topic = Literal[TOPICS]  # type: ignore[valid-type]


class LLMCitation(BaseModel):
    """A reference to exactly one of the numbered sources provided in the prompt."""

    source_id: str = Field(
        description="The bracket id of the retrieved source this citation points to, e.g. 'S1'."
    )


class LLMStatement(BaseModel):
    """One factual statement of the answer, grounded in the cited sources only."""

    text: str = Field(
        description="A single factual statement, containing exactly one claim or figure."
    )
    citations: list[LLMCitation] = Field(
        default_factory=list,
        description=(
            "Every source (by source_id) that supports this exact statement. "
            "Must contain at least one citation for the statement to be usable."
        ),
    )
    conflicting: bool = Field(
        default=False,
        description=(
            "Set to true only when this statement exists specifically to present one "
            "side of a disagreement between sources over the same fact — e.g. two "
            "documents reporting different values for the same period/metric. Each "
            "conflicting value gets its own statement citing only its own source(s)."
        ),
    )


class LLMGroundedAnswer(BaseModel):
    """The full structured-output contract returned by the generation model."""

    statements: list[LLMStatement] = Field(
        default_factory=list,
        description="The answer broken into independently-cited factual statements.",
    )
    unsupported: bool = Field(
        default=False,
        description=(
            "True if the provided sources do not contain enough information to answer "
            "the question at all. When true, statements should be empty."
        ),
    )
    self_confidence: Literal["high", "medium", "low"] = Field(
        default="high",
        description=(
            "How directly the SOURCES answer the question. 'high': the sources state the answer "
            "explicitly. 'medium': the answer needs combining or light interpretation of what "
            "the sources state. 'low': the sources only partially or indirectly address it, or "
            "the question asks for a judgment (e.g. an accounting treatment) the sources don't "
            "state outright."
        ),
    )


class LLMTriage(BaseModel):
    """ID-HU-BE-009: a short, focused pre-check run before answer generation.

    Kept separate from LLMGroundedAnswer on purpose: these checks are far
    more reliable as their own task than as more rules buried in the
    generation prompt. The model only reports *facts* about the sources and
    the question; whether the question is ambiguous is then decided in code
    (`clarification_for`), not by the model."""

    metric_in_sources: bool = Field(
        default=False,
        description="True if the SOURCES contain the figure or fact the QUESTION asks for.",
    )
    entities_with_metric: list[str] = Field(
        default_factory=list,
        description=(
            "Each distinct company/subsidiary/entity for which the SOURCES give the requested "
            "figure (as named in the sources or their file names). Empty if not found or if no "
            "entity is named."
        ),
    )
    periods_with_metric: list[str] = Field(
        default_factory=list,
        description=(
            "Each distinct period (year, quarter, month, date) for which the SOURCES give the "
            "requested figure, e.g. ['Q2 2024', 'Q3 2024']. Empty if not found."
        ),
    )
    question_specifies_entity: bool = Field(
        default=False,
        description=(
            "True if the QUESTION, read together with the CONVERSATION HISTORY, names which "
            "entity it means (a reply to an earlier clarification counts)."
        ),
    )
    question_specifies_period: bool = Field(
        default=False,
        description=(
            "True if the QUESTION, read together with the CONVERSATION HISTORY, names which "
            "period it means (a reply to an earlier clarification counts)."
        ),
    )
    requires_judgment: bool = Field(
        default=False,
        description=(
            "True when answering needs a professional judgment or recommendation (e.g. how "
            "revenue should be recognised, which accounting treatment applies, whether something "
            "is compliant) rather than reporting what the documents state."
        ),
    )
    topic: Topic = Field(
        default="general",
        description=(
            "The finance topic of the question: revenue_recognition, accounting, consolidation, "
            "tax, treasury, compensation, legal, budget_forecast, or general."
        ),
    )


@dataclass
class ResolvedCitation:
    """A citation resolved back to the real retrieved-chunk metadata."""

    source_id: str
    document_id: str
    document_filename: str
    sheet_name: str | None
    cell_range: str | None
    page_number: int | None
    reference_number: str | None
    text: str


@dataclass
class VersionUsed:
    """Which version of a document an answer's citations came from
    (ID-HU-BE-015: "the answer tells me which version was used")."""

    document_id: str
    filename: str
    version_number: int
    approval_status: str
    is_current: bool
    # Set when this version is not its group's default (i.e. the answer was
    # explicitly scoped to an older version for audit).
    current_version_filename: str | None = None


@dataclass
class ConfidenceOut:
    """ID-HU-BE-009: how much to trust an answer, and why."""

    level: str  # "high" | "medium" | "low"
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class GroundedStatement:
    text: str
    citations: list[ResolvedCitation] = field(default_factory=list)
    conflicting: bool = False
    notice: bool = False


@dataclass
class GroundedAnswer:
    question: str
    statements: list[GroundedStatement] = field(default_factory=list)
    sources: list[ResolvedCitation] = field(default_factory=list)
    has_conflicts: bool = False
    # False when nothing could be safely grounded — no retrieved sources, or the
    # model's statements were all dropped for lacking a valid citation.
    grounded: bool = False
    # The conversation this turn was recorded under (ID-HU-FE-001 follow-up
    # context) — set by qa/service.answer_question once the turn is persisted.
    session_id: str | None = None
    # ID-HU-BE-009: set instead of an answer when the question is ambiguous
    # given what's in the corpus (missing entity and/or period).
    needs_clarification: bool = False
    clarification_question: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    # ID-HU-BE-009: None when there's no answer to rate (clarification or no sources).
    confidence: ConfidenceOut | None = None
    # ID-HU-BE-009: suggested team when there's no confident answer.
    escalation: EscalationSuggestion | None = None
    restriction_notice: str | None = None
    versions_used: list[VersionUsed] = field(default_factory=list)
