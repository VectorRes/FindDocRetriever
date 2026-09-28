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

from pydantic import BaseModel, Field


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
    # Forward-compatible contract for ID-HU-BE-009 (ambiguous-question
    # clarification, implemented separately): always False/None until that
    # detection logic lands, so the frontend can build against this shape now.
    needs_clarification: bool = False
    clarification_question: str | None = None
    restriction_notice: str | None = None
    versions_used: list[VersionUsed] = field(default_factory=list)
