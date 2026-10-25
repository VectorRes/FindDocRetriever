"""Retrieval for QA questions: bilingual and spread across documents.

Wraps `retrieve()` (ID-HU-BE-007's similarity search, unchanged) with two
things the QA answers need:

- Cross-language: statements are in English or Spanish and so are the
  analysts' questions; "¿cuál era el efectivo?" doesn't land near "Cash and
  cash equivalents" in embedding space. The question is also searched in the
  other language (one short model call) and the results are merged.
- Diversity: one large document (a 30-page 10-Q) can fill the whole top-k
  and crowd out the others ("What was revenue?" returning only one company).
  When no document is selected, no document takes more than
  MAX_SHARE_PER_DOCUMENT of the sources while others have relevant ones.
- The company asked about: when the question names a company one or more
  documents are about ("total revenues of Micron", by the documents' file
  names), those documents are searched first and fill the sources; other
  documents only take the slots left. Otherwise another company's rows that
  match the question's words better ("Tesla … Total revenues") would crowd
  out the right company's figures.

Both only change which chunks are passed on; access control and auditing
stay with retrieve().
"""
import logging
import math
import re
import unicodedata
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Document, DocumentStatus
from app.qa.llm import get_chat_model
from app.retrieval.service import RetrievedChunk, retrieve

logger = logging.getLogger(__name__)

MAX_SHARE_PER_DOCUMENT = 0.6
_SPANISH = re.compile(
    r"[áéíóúñ¿¡]|\b(cu[aá]l|cu[aá]nto|qu[eé]|c[oó]mo|d[oó]nde|fue|fueron|era|ten[ií]a|utilidad|ingresos|ventas|"
    r"efectivo|activos|pasivos|patrimonio|trimestre|año|gastos)\b",
    re.IGNORECASE,
)

_ENGLISH = re.compile(
    r"\b(what|which|how|was|were|is|are|did|does|the|of|for|and|with|much|many|total|net|revenue|income|assets)\b",
    re.IGNORECASE,
)


def is_spanish(question: str) -> bool:
    """The question's language, from its words: a company name with an accent
    ("Fundación") doesn't make an English question Spanish."""
    spanish_words = len([m for m in _SPANISH.finditer(question) if m.group(1)])
    english_words = len(_ENGLISH.findall(question))
    if spanish_words != english_words:
        return spanish_words > english_words
    return bool(re.search(r"[¿¡]", question)) or (not english_words and bool(_SPANISH.search(question)))


TRANSLATE_PROMPT = """\
Translate the user's question about financial documents into {language}. \
Keep company names, figures, dates and accounting terms precise (e.g. \
"utilidad neta" <-> "net income", "efectivo" <-> "cash and cash equivalents"). \
Reply with the translated question only."""


def translate(question: str) -> str | None:
    """The question in the other language (English <-> Spanish), or None."""
    language = "English" if is_spanish(question) else "Spanish"
    try:
        reply = get_chat_model().invoke([
            {"role": "system", "content": TRANSLATE_PROMPT.format(language=language)},
            {"role": "user", "content": question},
        ])
    except Exception:  # a failed translation must not block answering
        logger.warning("Question translation failed; searching the original only", exc_info=True)
        return None
    text = " ".join(str(getattr(reply, "content", reply) or "").split())
    return text if text and text.lower() != question.lower() else None


def interleave(*rankings: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """One ranking from several: alternate between them, dropping repeats."""
    merged: list[RetrievedChunk] = []
    seen: set[str] = set()
    for position in range(max((len(r) for r in rankings), default=0)):
        for ranking in rankings:
            if position < len(ranking):
                chunk = ranking[position]
                key = chunk.chunk_id or f"{chunk.document_id}:{chunk.text[:80]}"
                if key not in seen:
                    seen.add(key)
                    merged.append(chunk)
    return merged


def diversify(ranked: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """The first `top_k` chunks, with at most MAX_SHARE_PER_DOCUMENT of them
    from one document while other documents have chunks to offer; slots no
    other document can use go back to the best remaining chunks."""
    cap = max(1, math.ceil(top_k * MAX_SHARE_PER_DOCUMENT))
    chosen: list[RetrievedChunk] = []
    held_back: list[RetrievedChunk] = []
    per_document: dict[str, int] = {}
    for chunk in ranked:
        if len(chosen) >= top_k:
            break
        if per_document.get(chunk.document_id, 0) >= cap:
            held_back.append(chunk)
            continue
        per_document[chunk.document_id] = per_document.get(chunk.document_id, 0) + 1
        chosen.append(chunk)
    for chunk in held_back:
        if len(chosen) >= top_k:
            break
        chosen.append(chunk)
    return chosen


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return set(re.findall(r"[a-z0-9]+", folded))


def named_documents(db: Session, question: str) -> list[UUID]:
    """The current documents of the company the question names, by the name
    their file gives ("EERR_Andina_2024.xlsx" -> Andina,
    "Micron-Technology-Inc.-MU-Q3-2026-Report.pdf" -> Micron)."""
    from app.qa.graph import entity_from_filename

    words = _words(question)
    named = []
    for document_id, filename in db.execute(
        select(Document.id, Document.filename).where(
            Document.is_current.is_(True), Document.status == DocumentStatus.ready.value)
    ).all():
        entity = entity_from_filename(filename)
        if entity and _words(entity) <= words:
            named.append(document_id)
    return named


def retrieve_for_question(
    db: Session,
    question: str,
    top_k: int | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    document_id: UUID | None = None,
) -> list[RetrievedChunk]:
    return retrieve_with_translation(db, question, top_k, user_roles, user_id, document_id)[0]


def retrieve_with_translation(
    db: Session,
    question: str,
    top_k: int | None = None,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
    document_id: UUID | None = None,
) -> tuple[list[RetrievedChunk], str | None]:
    """The chunks for `question`, and the question in the other language
    (None when translation is off or failed)."""
    settings = get_settings()
    k = top_k or settings.retrieval_top_k
    translated = translate(question) if settings.query_translation else None
    questions = [question, *([translated] if translated else [])]

    def search(document_ids=None, pool=k):
        return interleave(*(
            retrieve(db, question=q, top_k=pool, user_roles=user_roles, user_id=user_id,
                     document_id=document_id, document_ids=document_ids)
            for q in questions
        ))

    if document_id:
        return search()[:k], translated
    # The documents of the company the question names come first.
    named = named_documents(db, question)
    first = search(named)[:k] if named else []
    # Fetch more than k so diversifying has other documents' chunks to choose from.
    taken = {c.chunk_id for c in first}
    others = [c for c in search(pool=k * 2) if c.chunk_id not in taken]
    return first + diversify(others, k - len(first)), translated
