"""Answer confidence scoring (ID-HU-BE-009).

Combines a few explainable signals into a score in [0, 1] and a level
(high / medium / low), each penalty adding a human-readable reason:

  - grounding: how many of the model's statements had to be dropped for
    lacking a valid citation;
  - figure verification: every number in an answer statement must appear in
    the text of a source it cites (tolerating rounding and "3.4 million"-style
    scaling). The QA graph drops statements that fail it, so a fabricated
    figure never reaches the analyst; dropping them also lowers the score;
  - the model's self-reported confidence. "low" means the sources don't
    really answer the question (e.g. it asks for an accounting judgment the
    sources don't state), which is enough on its own to mark the answer low;
  - whether the question asks for a professional judgment (triage's
    `requires_judgment`), which documents alone can't settle;
  - retrieval relevance of the cited sources, *when* retrieval provides a
    `score` per chunk (ID-HU-BE-007's relevance score; ignored until then).
"""
import re

from app.qa.schemas import ConfidenceOut, GroundedStatement

HIGH_THRESHOLD = 0.8
MEDIUM_THRESHOLD = 0.6
# Below this average similarity the cited sources are considered weakly
# related. Placeholder until BE-007 calibrates scores against real embeddings.
WEAK_RELEVANCE = 0.5
# Relative tolerance when matching a stated figure to a source figure
# (covers rounding such as 3,401,234 stated as 3.4 million).
FIGURE_TOLERANCE = 0.02

_NUMBER = re.compile(r"(?<![\w.,])-?\d[\d.,]*\d(?![\w])|(?<![\w.,])\d(?![\w.,])")
_SCALE_WORDS = {
    "thousand": 1e3, "mil": 1e3, "k": 1e3,
    "million": 1e6, "millions": 1e6, "millón": 1e6, "millon": 1e6, "millones": 1e6, "m": 1e6, "mm": 1e6,
    "billion": 1e9, "billions": 1e9, "bn": 1e9,
}


def _interpretations(token: str) -> set[float]:
    """Numeric readings of a token, accepting both 1,234.5 and 1.234,5 styles."""
    readings: set[float] = set()
    candidates = [
        token.replace(",", ""),                       # 1,234.5 -> 1234.5
        token.replace(".", "").replace(",", "."),     # 1.234,5 -> 1234.5
    ]
    for candidate in candidates:
        try:
            readings.add(float(candidate))
        except ValueError:
            continue
    return readings


def _figures(text: str) -> list[tuple[str, set[float]]]:
    """Every number in `text`: its literal token and its set of possible
    values (with scale words such as "million" applied)."""
    figures = []
    for match in _NUMBER.finditer(text):
        values = _interpretations(match.group())
        following = text[match.end():match.end() + 12].strip().lower()
        scale_word = re.match(r"[a-záéíóú]+", following)
        if scale_word and scale_word.group() in _SCALE_WORDS:
            factor = _SCALE_WORDS[scale_word.group()]
            values |= {v * factor for v in values}
        figures.append((match.group(), values))
    return figures


def _is_material(token: str) -> bool:
    """Skip numbers that are labels rather than figures: years, quarters, days."""
    if re.fullmatch(r"-?\d+", token):
        value = int(token)
        return not (abs(value) < 100 or 1900 <= value <= 2100)
    return True


def _matches(stated: set[float], source_values: set[float]) -> bool:
    for s in stated:
        for v in source_values:
            if v == s or (v != 0 and abs(s - v) / abs(v) <= FIGURE_TOLERANCE):
                return True
            # A source in thousands/millions ("in thousands of COP") stated in full, or vice versa.
            for factor in (1e3, 1e6):
                if v != 0 and abs(s - v * factor) / abs(v * factor) <= FIGURE_TOLERANCE:
                    return True
    return False


def primary_reading(token: str) -> float:
    """The most plausible single value of a number token, for when one value
    is needed rather than all readings: the last of ',' / '.' is the decimal
    mark; a lone separator followed by groups of exactly 3 digits is a
    thousands separator ("81,000", "4.820.000"), unless the number starts
    with 0 ("0.125")."""
    negative = token.startswith("-")
    digits = token.lstrip("-")
    if "," in digits and "." in digits:
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
    elif "," in digits or "." in digits:
        separator = "," if "," in digits else "."
        is_grouping = re.fullmatch(rf"\d{{1,3}}(\{separator}\d{{3}})+", digits) and not digits.startswith("0")
        decimal = None if is_grouping else separator
    else:
        decimal = None
    thousands = {",", "."} - {decimal}
    normalized = "".join(ch for ch in digits if ch not in thousands)
    if decimal:
        normalized = normalized.replace(decimal, ".")
    value = float(normalized)
    return -value if negative else value


def match_figure(value_text: str, source_text: str) -> float | None:
    """The value of `value_text` (its primary reading) if that figure appears
    in `source_text`, else None. Used by ID-HU-FE-003's figure extraction so
    a compared value is always one the source actually contains."""
    match = _NUMBER.search(value_text)
    if match is None:
        return None
    value = primary_reading(match.group())
    source_values: set[float] = set()
    for _, values in _figures(source_text):
        source_values |= values
    return value if _matches({value}, source_values) else None


def unverified_figures(statement: GroundedStatement) -> list[str]:
    """Figures in `statement` that don't appear in any source it cites."""
    source_values: set[float] = set()
    for citation in statement.citations:
        for _, values in _figures(citation.text):
            source_values |= values

    missing = []
    for token, values in _figures(statement.text):
        if _is_material(token) and not _matches(values, source_values):
            missing.append(token)
    return missing


def assess_confidence(
    kept_count: int,
    proposed_count: int,
    removed_figures: list[str],
    self_confidence: str,
    cited_scores: list[float],
    requires_judgment: bool = False,
) -> ConfidenceOut:
    """Score an answer where `kept_count` of the model's `proposed_count`
    statements survived grounding. `removed_figures` are the figures whose
    statements were dropped because the figure wasn't in their sources."""
    score = 1.0
    reasons: list[str] = []

    dropped = max(proposed_count - kept_count, 0)
    if proposed_count and dropped:
        score -= 0.5 * dropped / proposed_count
        reasons.append(f"{dropped} of {proposed_count} statements could not be verified and were removed")

    if removed_figures:
        # A figure the sources don't contain is the strongest fabrication signal.
        score -= 0.3
        reasons.append(
            "Figures not found in the cited sources were removed: "
            + ", ".join(dict.fromkeys(removed_figures))
        )

    if self_confidence == "low":
        score -= 0.45
        reasons.append("The sources only partially or indirectly address the question")
    elif self_confidence == "medium":
        score -= 0.25

    if requires_judgment:
        # Documents can report facts, but a treatment/compliance call belongs
        # to the team that owns it, so it's never presented as a confident answer.
        score -= 0.45
        reasons.append(
            "The question asks for a professional judgment (e.g. an accounting treatment) "
            "that the documents alone can't settle"
        )

    if cited_scores:
        average = sum(cited_scores) / len(cited_scores)
        if average < WEAK_RELEVANCE:
            score -= 0.2
            reasons.append("The cited sources are only weakly related to the question")

    score = round(min(max(score, 0.0), 1.0), 2)
    level = "high" if score >= HIGH_THRESHOLD else "medium" if score >= MEDIUM_THRESHOLD else "low"
    return ConfidenceOut(level=level, score=score, reasons=reasons)
