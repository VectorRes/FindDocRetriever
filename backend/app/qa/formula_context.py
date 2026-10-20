"""Formula precedents as extra sources for QA answers.

Excel chunks show a cell's formula ("2025 presupuesto=723723 (formula:
=C5-C6)"), but similarity search won't necessarily retrieve the rows C5 and
C6 point to, so the model can't say what the formula adds up. After retrieval,
`add_formula_precedents` appends the chunks holding each formula's direct
precedents — the cells it references and the cells of named ranges it uses
(e.g. TasaImpuesto -> Supuestos!$B$5) — so "how is X calculated?" can be
answered with each component's line item and value, cited.

Precedents come from the same document as the formula (so the same version),
must be accessible to the caller, and are audited like any retrieved chunk.
"""
import re
from uuid import UUID

from openpyxl.utils import coordinate_to_tuple, range_boundaries
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import Denial, record_denials, record_granted
from app.db.models import Cell, Chunk, NamedRange, Sheet
from app.qa.query_expansion import is_spanish
from app.retrieval.access import can_access, parse_roles
from app.retrieval.service import RetrievedChunk

# Keeps the prompt small: a formula rarely needs more than a handful of rows.
MAX_PRECEDENTS = 6
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _precedent_cells(db: Session, chunk: RetrievedChunk) -> list[tuple[str, int, int, str | None]]:
    """(sheet, row, column, note) for every cell the chunk's formulas reference."""
    min_col, min_row, max_col, max_row = range_boundaries(chunk.cell_range)
    document_id = UUID(chunk.document_id)
    cells = db.scalars(
        select(Cell).join(Sheet).where(
            Sheet.document_id == document_id,
            Sheet.name == chunk.sheet_name,
            Cell.formula.is_not(None),
            Cell.row.between(min_row, max_row),
            Cell.column.between(min_col, max_col),
        )
    ).all()

    found: list[tuple[str, int, int, str | None]] = []
    for cell in cells:
        found.extend(_references(db, document_id, chunk.sheet_name, cell))
    return found


def _references(db: Session, document_id: UUID, sheet: str, cell: Cell) -> list[tuple[str, int, int, str | None]]:
    """(sheet, row, column, note) for each cell one formula references; `note`
    defines the named range the reference came through, if any."""
    found: list[tuple[str, int, int, str | None]] = []
    for reference in cell.formula_references or []:
        ref_sheet, _, address = reference.rpartition("!")
        row, column = coordinate_to_tuple(address.replace("$", ""))
        found.append((ref_sheet or sheet, row, column, None))
    names = set(_NAME.findall(cell.formula or ""))
    if names:
        for named in db.scalars(
            select(NamedRange).where(NamedRange.document_id == document_id, NamedRange.name.in_(names))
        ):
            first_cell = named.cell_range.replace("$", "").split(":")[0]
            row, column = coordinate_to_tuple(first_cell)
            named_sheet = named.sheet_name or sheet
            note = f"Named range {named.name} = {named_sheet}!{named.cell_range}."
            found.append((named_sheet, row, column, note))
    return found


def _chunk_covering(db: Session, document_id: str, sheet: str, row: int, column: int) -> Chunk | None:
    candidates = db.scalars(
        select(Chunk).where(
            Chunk.document_id == UUID(document_id),
            Chunk.sheet_name == sheet,
            Chunk.cell_range.is_not(None),
        )
    )
    for candidate in candidates:
        min_col, min_row, max_col, max_row = range_boundaries(candidate.cell_range)
        if min_row <= row <= max_row and min_col <= column <= max_col:
            return candidate
    return None


def _to_retrieved(chunk: Chunk, note: str | None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        document_filename=chunk.document.filename,
        sheet_name=chunk.sheet_name,
        cell_range=chunk.cell_range,
        page_number=chunk.page_number,
        # The named-range definition comes from the workbook itself, not the model.
        text=f"{note} {chunk.text}" if note else chunk.text,
        content_type=chunk.content_type,
        extraction_method=chunk.extraction_method,
        confidence=chunk.confidence,
        confidence_label=chunk.confidence_label,
        confidentiality_tag=chunk.confidentiality_tag,
    )


_CALCULATION_QUESTION = re.compile(
    r"c[oó]mo se (calcula|calcul[oó]|obtiene|obtuvo|compone|llega)|de d[oó]nde sale|f[oó]rmula|desglose"
    r"|how (is|was|are|were|do|does|did) .*(calculat|comput|derive|arriv|built)|formula|breakdown",
    re.IGNORECASE,
)
MAX_EXPLAINED_FORMULAS = 2


def asks_for_calculation(question: str) -> bool:
    return bool(_CALCULATION_QUESTION.search(question))


def _format_value(value: str | None) -> str:
    if value is None:
        return "(empty)"
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer() and abs(number) >= 1000:
        return f"{int(number):,}"
    return value


def _row_label(db: Session, document_id: UUID, sheet: str, row: int, column: int) -> str | None:
    """The line item of a cell: the leftmost text cell of its row, before it."""
    cells = db.scalars(
        select(Cell).join(Sheet).where(
            Sheet.document_id == document_id, Sheet.name == sheet, Cell.row == row, Cell.column < column,
        ).order_by(Cell.column)
    )
    for cell in cells:
        if cell.value and not _is_number(cell.value):
            return cell.value
    return None


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _join(parts: list[str], spanish: bool) -> str:
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])}{' y ' if spanish else ' and '}{parts[-1]}"


def _cell(db: Session, document_id: UUID, sheet: str, row: int, column: int) -> Cell | None:
    return db.scalar(
        select(Cell).join(Sheet).where(
            Sheet.document_id == document_id, Sheet.name == sheet, Cell.row == row, Cell.column == column,
        )
    )


def explain_formulas(db: Session, question: str, statements: list, sources: list) -> list:
    """Statements explaining the formulas behind the answer, built from the
    workbook itself (formula, referenced cells, their line items and values)
    rather than left to the model, which doesn't reliably do it.

    Only for questions asking how something is calculated, for formula cells
    in sources the answer cites, and only when the answer doesn't already
    cite the formula's components. Each explanation cites the formula's
    source and every component's source; components outside the given
    sources are not described."""
    from app.qa.schemas import GroundedStatement  # avoid an import cycle with app.qa.graph

    if not asks_for_calculation(question):
        return []
    spanish = is_spanish(question)
    by_location = {(s.document_id, s.sheet_name, s.cell_range): s for s in sources if s.cell_range}
    cited = {
        (c.document_id, c.sheet_name, c.cell_range)
        for statement in statements for c in statement.citations if c.cell_range
    }

    def source_for(document_id: str, sheet: str, row: int, column: int):
        for (doc, sheet_name, cell_range), source in by_location.items():
            if doc != document_id or sheet_name != sheet:
                continue
            min_col, min_row, max_col, max_row = range_boundaries(cell_range)
            if min_row <= row <= max_row and min_col <= column <= max_col:
                return source
        return None

    # Every formula cell in the cited sources, and every cell those formulas use.
    formulas: list[tuple[str, str, str, Cell]] = []
    used: set[tuple[str, str, int, int]] = set()
    for document_id, sheet, cell_range in sorted(cited, key=lambda k: tuple(map(str, k))):
        doc_uuid = UUID(document_id)
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
        for cell in db.scalars(
            select(Cell).join(Sheet).where(
                Sheet.document_id == doc_uuid, Sheet.name == sheet, Cell.formula.is_not(None),
                Cell.row.between(min_row, max_row), Cell.column.between(min_col, max_col),
            ).order_by(Cell.row, Cell.column)
        ):
            formulas.append((document_id, sheet, cell_range, cell))
            used.update((document_id, s, r, c) for s, r, c, _ in _references(db, doc_uuid, sheet, cell))

    explanations = []
    for document_id, sheet, cell_range, cell in formulas:
        if len(explanations) >= MAX_EXPLAINED_FORMULAS:
            break
        # Only the figure asked about: a cited cell that's an input of another
        # cited formula (operating income, inside net income) isn't explained.
        if (document_id, sheet, cell.row, cell.column) in used:
            continue
        doc_uuid = UUID(document_id)
        parts, citations = [], [by_location[(document_id, sheet, cell_range)]]
        for sheet_name, row, column, note in _references(db, doc_uuid, sheet, cell):
            source = source_for(document_id, sheet_name, row, column)
            precedent = _cell(db, doc_uuid, sheet_name, row, column)
            if source is None or precedent is None:
                continue
            label = _row_label(db, doc_uuid, sheet_name, row, column) or precedent.address
            name = note.split(" = ")[0].removeprefix("Named range ") if note else (
                precedent.address if sheet_name == sheet else f"{sheet_name}!{precedent.address}"
            )
            parts.append(f"{name} = {label} ({_format_value(precedent.value)})")
            if source not in citations:
                citations.append(source)
        if not parts or any(
            (c.document_id, c.sheet_name, c.cell_range) in cited for c in citations[1:]
        ):
            continue  # nothing to explain, or the model already cited the components
        label = _row_label(db, doc_uuid, sheet, cell.row, cell.column) or cell.address
        value = _format_value(cell.value)
        joined = _join(parts, spanish)
        text = (
            f"Cálculo de {label} ({value}): {cell.formula}, donde {joined}."
            if spanish else
            f"How {label} ({value}) is calculated: {cell.formula}, where {joined}."
        )
        explanations.append(GroundedStatement(text=text, citations=citations))
    return explanations


def add_formula_precedents(
    db: Session,
    chunks: list[RetrievedChunk],
    question: str,
    user_roles: str | None = "analyst",
    user_id: str | None = "anonymous",
) -> list[RetrievedChunk]:
    """`chunks` plus the chunks holding their formulas' direct precedents."""
    roles = parse_roles(user_roles)
    seen = {chunk.chunk_id for chunk in chunks}
    added: list[RetrievedChunk] = []
    denied: list[Denial] = []
    for chunk in chunks:
        if len(added) >= MAX_PRECEDENTS:
            break
        if "(formula:" not in chunk.text or not chunk.sheet_name or not chunk.cell_range:
            continue
        for sheet, row, column, note in _precedent_cells(db, chunk):
            precedent = _chunk_covering(db, chunk.document_id, sheet, row, column)
            if precedent is None or str(precedent.id) in seen:
                continue
            seen.add(str(precedent.id))
            if not can_access(precedent.confidentiality_tag, roles):
                # ID-HU-BE-014: the formula relies on a section the user can't see.
                denied.append(Denial(
                    document_id=precedent.document_id, document_filename=chunk.document_filename,
                    chunk_id=precedent.id, tag=precedent.confidentiality_tag, action="lookup",
                ))
                continue
            added.append(_to_retrieved(precedent, note))
            record_granted(
                db, user_id=user_id, roles=roles, document_id=precedent.document_id, chunk_id=precedent.id,
                document_filename=chunk.document_filename, tag=precedent.confidentiality_tag,
                question=question, action="lookup",
            )
            if len(added) >= MAX_PRECEDENTS:
                break
    if added:
        db.flush()
    record_denials(user_id=user_id, roles=roles, denials=denied, question=question)
    return chunks + added
