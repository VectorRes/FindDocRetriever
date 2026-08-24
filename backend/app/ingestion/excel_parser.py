"""Excel workbook parsing: sheets, cells, formulas, named ranges and warnings.

Covers user stories BE-001 (sheet/cell extraction), BE-002 (formulas, named
ranges, cross-sheet references, circular references) and BE-003 (macros,
external links, protected sheets).
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

# Matches an (optionally sheet-qualified) A1-style cell reference, e.g.
# "B5", "$B$5", "Assumptions!B5", "'Cash Flow'!$C$2". The trailing
# negative lookahead excludes function names like "LOG10(" from matching.
CELL_REF_RE = re.compile(
    r"(?:'([^']+)'|([A-Za-z0-9_ ]+))?!?\$?([A-Z]{1,3})\$?(\d{1,7})(?!\()"
)

# Matches bracketed external-workbook references in a formula, e.g.
# "[Q2_Actuals.xlsx]Sheet1!A1" or "'[Budget.xlsx]Sheet1'!$A$1".
EXTERNAL_LINK_FORMULA_RE = re.compile(r"\[([^\[\]]+\.xls[xmb]?)\]")


class ExcelParsingError(Exception):
    """Raised when a workbook cannot be opened/parsed at all."""


@dataclass
class ParsedCell:
    address: str
    row: int
    column: int
    value: str | None
    formula: str | None
    formula_references: list[str] = field(default_factory=list)
    is_merged: bool = False
    merged_range: str | None = None


@dataclass
class ParsedSheet:
    name: str
    is_empty: bool = False
    is_protected: bool = False
    cells: list[ParsedCell] = field(default_factory=list)


@dataclass
class ParsedNamedRange:
    name: str
    sheet_name: str | None
    cell_range: str


@dataclass
class ParsedWorkbook:
    sheets: list[ParsedSheet] = field(default_factory=list)
    named_ranges: list[ParsedNamedRange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _extract_formula_references(formula: str, current_sheet: str) -> list[str]:
    refs: set[str] = set()
    for quoted, unquoted, col, row in CELL_REF_RE.findall(formula):
        sheet = quoted or unquoted or current_sheet
        refs.add(f"{sheet}!{col}{row}")
    return sorted(refs)


def _detect_external_links(workbook) -> list[str]:
    links = []
    for link in getattr(workbook, "_external_links", []) or []:
        target = getattr(getattr(link, "file_link", None), "Target", None)
        if target:
            links.append(target)
    return links


def _has_macros(path: Path) -> bool:
    # openpyxl's `vba_archive` is set to a ZipFile whenever keep_vba=True is
    # passed, even for plain .xlsx files with no macros — it's not a signal
    # of macro presence. Check the zip contents directly instead.
    if path.suffix.lower() in (".xlsm", ".xltm", ".xlam"):
        return True
    try:
        with zipfile.ZipFile(path) as zf:
            return "xl/vbaProject.bin" in zf.namelist()
    except zipfile.BadZipFile:
        return False


def _detect_circular_references(sheets: list[ParsedSheet]) -> list[str]:
    """Static-analysis cycle detection over the single-cell formula dependency graph."""
    graph: dict[str, list[str]] = {}
    for sheet in sheets:
        for cell in sheet.cells:
            if cell.formula:
                graph[f"{sheet.name}!{cell.address}"] = cell.formula_references

    warnings: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        if node in visiting:
            cycle = " -> ".join(path[path.index(node):] + [node])
            warnings.append(f"Circular reference detected: {cycle}")
            return
        if node in visited or node not in graph:
            return
        visiting.add(node)
        for neighbor in graph[node]:
            dfs(neighbor, path + [node])
        visiting.discard(node)
        visited.add(node)

    for start in graph:
        if start not in visited:
            dfs(start, [])
    return warnings


def parse_excel(path: str | Path) -> ParsedWorkbook:
    path = Path(path)
    try:
        wb_formulas = openpyxl.load_workbook(path, data_only=False)
        wb_values = openpyxl.load_workbook(path, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ExcelParsingError(f"Could not open workbook '{path.name}': {exc}") from exc

    result = ParsedWorkbook()

    if _has_macros(path):
        result.warnings.append(
            f"Workbook '{path.name}' contains macros; macros were not executed and require manual review."
        )

    external_links: set[str] = set(_detect_external_links(wb_formulas))

    for name, defn in wb_formulas.defined_names.items():
        for sheet_title, coordinate in defn.destinations:
            result.named_ranges.append(
                ParsedNamedRange(name=name, sheet_name=sheet_title, cell_range=coordinate)
            )

    for sheet_name in wb_formulas.sheetnames:
        ws_formulas = wb_formulas[sheet_name]
        ws_values = wb_values[sheet_name]

        is_protected = bool(ws_formulas.protection and ws_formulas.protection.sheet)
        if is_protected:
            result.warnings.append(
                f"Sheet '{sheet_name}' is password-protected: not fully parsed."
            )

        merged_value_by_cell: dict[str, tuple[str | None, str]] = {}
        for merged_range in ws_formulas.merged_cells.ranges:
            top_left = ws_formulas.cell(row=merged_range.min_row, column=merged_range.min_col)
            top_left_value = top_left.value
            range_str = str(merged_range)
            for row in range(merged_range.min_row, merged_range.max_row + 1):
                for col in range(merged_range.min_col, merged_range.max_col + 1):
                    addr = ws_formulas.cell(row=row, column=col).coordinate
                    merged_value_by_cell[addr] = (
                        top_left_value if top_left_value is None else str(top_left_value),
                        range_str,
                    )

        cells: list[ParsedCell] = []
        any_value = False
        for row in ws_formulas.iter_rows():
            for cell in row:
                addr = cell.coordinate
                is_formula = isinstance(cell.value, str) and cell.value.startswith("=")
                formula = cell.value if is_formula else None
                formula_refs = (
                    _extract_formula_references(formula, sheet_name) if formula else []
                )
                if formula:
                    external_links.update(EXTERNAL_LINK_FORMULA_RE.findall(formula))

                if addr in merged_value_by_cell:
                    value, merged_range_str = merged_value_by_cell[addr]
                    is_merged = True
                else:
                    raw_value = ws_values[addr].value
                    value = None if raw_value is None else str(raw_value)
                    merged_range_str = None
                    is_merged = False

                if value is not None or formula is not None:
                    any_value = True

                if value is None and formula is None and not is_merged:
                    continue

                cells.append(
                    ParsedCell(
                        address=addr,
                        row=cell.row,
                        column=cell.column,
                        value=value,
                        formula=formula,
                        formula_references=formula_refs,
                        is_merged=is_merged,
                        merged_range=merged_range_str,
                    )
                )

        result.sheets.append(
            ParsedSheet(
                name=sheet_name,
                is_empty=not any_value,
                is_protected=is_protected,
                cells=cells,
            )
        )

    for target in sorted(external_links):
        result.warnings.append(f"External link detected and left unresolved: {target}")

    result.warnings.extend(_detect_circular_references(result.sheets))
    return result
