"""What a chunk's embedding is computed from: the chunk with its document.

A table row extracted from a PDF often doesn't say whose figures it holds
("Financial table page 4, row 'Revenue': column 1=9,301; column 2=41,456"),
so a question naming the company ("total revenues of Micron") lands closer
to another company's row that matches the words ("Tesla … row 'Total
revenues'"). Each chunk is therefore embedded with its document's name in
front ("Micron Technology Inc. MU Q3 2026 Report"). Only the embedding gets
it: the stored text — what citations show, figures are verified against and
row labels are parsed from — stays as extracted.

Documents ingested before this are re-embedded with
`python -m app.ingestion.reembed`.
"""
import re
from pathlib import PurePath

_SEPARATORS = re.compile(r"[_\-]+")


def document_label(filename: str) -> str:
    """The document as a title: "Micron-Technology-Inc.-MU-Q3-2026-Report.pdf"
    -> "Micron Technology Inc. MU Q3 2026 Report"."""
    stem = PurePath(filename).stem
    return " ".join(_SEPARATORS.sub(" ", stem).split()) or filename


def embedding_text(filename: str, text: str) -> str:
    """What to embed for a chunk of the document `filename`."""
    return f"Document: {document_label(filename)}\n{text}"
