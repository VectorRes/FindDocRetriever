"""Line-item mapping from parsed statements to formula inputs (ID-HU-BE-010).

The figure itself comes from FE-003's `extract_figure`, which guarantees the
value appears in its cited source. What this module adds is the check the
ratio needs on top: *is the line item we found really the formula input?*

`label_matches` compares the label as written in the statement against the
line item's accepted labels (formulas.LINE_ITEMS). The comparison is
deliberately strict — exact match after normalizing accents, case,
punctuation, years and a leading "Total" — so "Total current liabilities"
is accepted but "Short-Term Obligations" or "Non-current liabilities" are
not. A label that doesn't match makes the mapping low-confidence and the
ratio waits for the analyst's review (alternate case 2).
"""
import re
import unicodedata

from app.ratios.formulas import LineItem, Statement

# Filler words ignored when comparing labels.
_FILLER = {"total", "de", "del", "la", "el", "los", "las", "y", "and", "the", "of", "for"}

# Words in a document's filename or sheet names hinting which statement it holds,
# used to try the likeliest document first.
_STATEMENT_HINTS: dict[Statement, tuple[str, ...]] = {
    "income_statement": (
        "income statement", "income", "p l", "pnl", "pyg", "profit and loss", "profit loss",
        "estado de resultados", "resultados", "eerr", "perdidas y ganancias",
    ),
    "balance_sheet": (
        "balance sheet", "balance", "bs", "financial position", "situacion financiera", "esf",
        "estado de situacion",
    ),
}


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\([^)]*\)", " ", text)       # "(COP)", "(in thousands)"
    return re.findall(r"[a-z]+", text)            # drops digits: years, note numbers


def normalize_label(label: str) -> str:
    words = [word for word in _words(label) if word not in _FILLER]
    # US GAAP's ", net" suffix ("Accounts receivable, net") names the same line item.
    if len(words) > 1 and words[-1] == "net":
        words = words[:-1]
    return " ".join(words)


def label_matches(item: LineItem, label: str | None) -> bool:
    if not label:
        return False
    normalized = normalize_label(label)
    return bool(normalized) and normalized in {normalize_label(accepted) for accepted in item.accepted_labels}


def statement_hint_score(statement: Statement, names: list[str]) -> int:
    """How strongly a document's filename/sheet names suggest `statement`."""
    text = f" {' '.join(' '.join(_words(name.replace('_', ' '))) for name in names)} "
    return sum(1 for hint in _STATEMENT_HINTS[statement] if f" {hint} " in text)
