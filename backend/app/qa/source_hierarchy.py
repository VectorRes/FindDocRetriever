"""Source-of-truth hierarchy in answers (ID-HU-BE-016).

1. `annotate_sources` tags every retrieved source with its document's tier
   and approval, and orders the sources ERP/EPM first, then official
   reports, then internal reports (relevance order kept within a tier). The
   generation prompt shows each source's tier and asks for the highest one.
2. `set_aside_lower_tiers` enforces it in code: when triage found the
   requested figure in sources of different tiers for the same entity and
   period, the lower-tier ones are removed before the answer is written. The
   answer is then based on the ERP/EPM data even if an internal report gives
   the same figure.
3. `source_notices` tells the analyst how much to trust the result:
     - "draft": a cited document hasn't been approved;
     - "lower_tier": a cited source isn't ERP/EPM, because no higher-tier
       source covered the figure, so it hasn't been reconciled against the
       source of truth;
     - "higher_tier_preferred": a lower-tier source also covered the figure
       but wasn't used as the primary one.
"""
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy.orm import Session

from app.figures.extraction import entity_key, period_key
from app.qa.schemas import GroundedAnswer, LLMTriage, SourceNotice
from app.retrieval.service import RetrievedChunk
from app.source_tiers import ERP_EPM, INTERNAL_REPORT, TIER_NAMES, tier_rank
from app.versioning import APPROVED


@dataclass
class SetAside:
    """A lower-tier source removed in favour of a higher-tier one."""

    source: RetrievedChunk
    preferred: RetrievedChunk


def annotate_sources(db: Session, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """`chunks` with their document's tier and approval, highest tier first."""
    from app.db.models import Document

    documents: dict[str, object] = {}
    annotated = []
    for chunk in chunks:
        if chunk.document_id not in documents:
            documents[chunk.document_id] = db.get(Document, UUID(chunk.document_id))
        document = documents[chunk.document_id]
        annotated.append(replace(
            chunk,
            source_tier=getattr(document, "source_tier", INTERNAL_REPORT),
            approved=getattr(document, "approval_status", None) == APPROVED,
        ))
    return sorted(annotated, key=lambda c: tier_rank(c.source_tier))


def _same_figure(a, b) -> bool:
    """Whether two triage entries are the same figure: the same period, and
    the same entity unless one of them doesn't say."""
    if not a.period or not b.period or period_key(a.period) != period_key(b.period):
        return False
    return not a.entity or not b.entity or entity_key(a.entity) == entity_key(b.entity)


def set_aside_lower_tiers(
    triage: LLMTriage, source_map: dict[str, RetrievedChunk]
) -> tuple[dict[str, RetrievedChunk], list[SetAside]]:
    """The sources to answer from, without the lower-tier ones that give the
    requested figure for the same entity and period as a higher-tier source;
    and those set aside, with the source preferred to each."""
    entries = [m for m in triage.metric_sources if m.source_id in source_map]
    set_aside: dict[str, SetAside] = {}
    for entry in entries:
        chunk = source_map[entry.source_id]
        better = [
            other for other in entries
            if tier_rank(source_map[other.source_id].source_tier) < tier_rank(chunk.source_tier)
            and _same_figure(entry, other)
        ]
        if better:
            best = min(better, key=lambda other: tier_rank(source_map[other.source_id].source_tier))
            set_aside[entry.source_id] = SetAside(chunk, source_map[best.source_id])
    kept = {sid: chunk for sid, chunk in source_map.items() if sid not in set_aside}
    return kept, list(set_aside.values())


def _names(filenames: list[str]) -> str:
    return ", ".join(filenames)


def _describe_tiers(tiers: list[str]) -> str:
    """["internal_report"] -> "an internal report"; two of them -> "internal reports"."""
    parts = []
    for tier in sorted(set(tiers), key=tier_rank):
        name = TIER_NAMES.get(tier, tier)
        parts.append(f"{name}s" if tiers.count(tier) > 1 else f"{'an' if name[0] in 'aeiou' else 'a'} {name}")
    return " and ".join(parts)


def source_notices(answer: GroundedAnswer, sources: dict[str, RetrievedChunk], set_aside: list[SetAside]) -> list[SourceNotice]:
    """What the analyst should know about the sources behind `answer`
    (see module docstring). `sources` maps document id to one of its
    annotated chunks."""
    if not answer.grounded or answer.needs_clarification:
        return []
    cited: list[str] = []
    for statement in answer.statements:
        for citation in statement.citations:
            if citation.document_id not in cited and citation.document_id in sources:
                cited.append(citation.document_id)
    if not cited:
        return []
    notices: list[SourceNotice] = []

    drafts = [sources[d].document_filename for d in cited if not sources[d].approved]
    if drafts:
        one = len(drafts) == 1
        notices.append(SourceNotice(
            kind="draft",
            message=(
                f"Based on {'a draft document' if one else 'draft documents'} not yet approved: {_names(drafts)}. "
                f"{'Its' if one else 'Their'} figures are provisional, not approved figures."
            ),
            document_filenames=drafts,
        ))

    lower = [d for d in cited if sources[d].source_tier != ERP_EPM]
    if lower:
        filenames = [sources[d].document_filename for d in lower]
        described = _describe_tiers([sources[d].source_tier for d in lower])
        if len(lower) == len(cited):
            message = (
                f"No ERP/EPM source covering this was available, so the answer is based on {described} "
                f"({_names(filenames)}). These figures haven't been reconciled against the ERP/EPM source of truth."
            )
        else:
            message = (
                f"Part of this answer comes from {described} ({_names(filenames)}) because no ERP/EPM source covered "
                "those figures; they haven't been reconciled against the ERP/EPM source of truth."
            )
        notices.append(SourceNotice(kind="lower_tier", message=message, document_filenames=filenames))

    by_document: dict[str, SetAside] = {}
    for item in set_aside:
        by_document.setdefault(item.source.document_id, item)
    for item in by_document.values():
        source, preferred = item.source, item.preferred
        notices.append(SourceNotice(
            kind="higher_tier_preferred",
            message=(
                f"{source.document_filename} ({TIER_NAMES.get(source.source_tier, source.source_tier)}) also covers "
                f"this figure but wasn't used as the primary source: {preferred.document_filename} "
                f"({TIER_NAMES.get(preferred.source_tier, preferred.source_tier)}) ranks higher."
            ),
            document_filenames=[source.document_filename],
        ))
    return notices
