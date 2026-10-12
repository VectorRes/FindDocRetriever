"""Chat answers grounded in the statement line asked about, for any document.

Mechanisms found failing on real filings: a note's "Total" taken as total
assets, an English question misread as Spanish because a name has an
accent, a total and its breakdown flagged as "conflicting", and a growth
percentage repeated as an amount.
"""
from app.db.models import Chunk, Document
from app.qa.confidence import unverified_figures
from app.qa.graph import same_line, statement_row_mismatch
from app.qa.query_expansion import is_spanish
from app.qa.schemas import GroundedStatement, ResolvedCitation
from app.qa.statement_rows import row_figures, statement_rows
from app.retrieval.service import RetrievedChunk

BALANCE_ROW = ("Financial table page 2 (FUNDACIÓN BANCOLOMBIA ESTADO DE SITUACIÓN FINANCIERA (Cifras en miles)), "
               "row 'Total activo': 31/12/2024=166,568,554; 31/12/2023=133,375,329; Variación absoluta=33,193,224")
NOTE_TEXT = ("6.8 Encargo fiduciario El saldo de esta cuenta a 31 de diciembre de 2024 está conformado por: "
             "Encargo fiduciario 115,321,854 101,828,343 Total $115,321,854 $101,828,343")


def _citation(text, document_id="doc", page=2):
    return ResolvedCitation(source_id="S1", document_id=document_id, document_filename="eeff.pdf", sheet_name=None,
                            cell_range=None, page_number=page, reference_number=None, text=text)


def _row(text, document_id="doc", page=2):
    return RetrievedChunk(document_id=document_id, document_filename="eeff.pdf", sheet_name=None, cell_range=None,
                          page_number=page, text=text, chunk_id=f"{document_id}-{page}")


def _statement(text, *citations):
    return GroundedStatement(text=text, citations=list(citations))


# --- language -------------------------------------------------------------------------------------

def test_language_comes_from_the_words_not_an_accented_name():
    assert not is_spanish("What were Fundación Bancolombia's financial expenses in 2024?")
    assert is_spanish("¿Cuál fue el total de activos de la Fundación Bancolombia en 2024?")
    assert is_spanish("¿Cuántos activos totales tenía Amazon a diciembre de 2025?")
    assert is_spanish("Utilidad neta Andina 2024")
    assert not is_spanish("What was Meta's revenue in Q2 2026?")


# --- the statement line asked about ---------------------------------------------------------------

def test_answer_from_a_notes_total_is_flagged_against_the_statement_line():
    answer = [_statement("El total de activos a 31 de diciembre de 2024 es $115,321,854.", _citation(NOTE_TEXT, page=18))]
    reason = statement_row_mismatch(answer, [_row(BALANCE_ROW)])
    assert reason and "Total activo" in reason


def test_answer_matching_any_column_of_the_statement_line_passes():
    for text in ("El total de activos en 2024 es 166,568,554.", "Total assets were 133,375,329 thousand in 2023.",
                 "Total assets were 166,568,554 miles de pesos."):
        assert statement_row_mismatch([_statement(text, _citation(BALANCE_ROW))], [_row(BALANCE_ROW)]) is None


def test_statement_line_of_another_document_is_not_compared():
    answer = [_statement("Total assets were 921,983 million.", _citation("row 'Total assets': 2026=921,983", "other"))]
    assert statement_row_mismatch(answer, [_row(BALANCE_ROW)]) is None


def test_row_figures_reads_every_column():
    assert {166_568_554, 133_375_329, 33_193_224} <= row_figures(_row(BALANCE_ROW))


def test_rows_named_by_the_question_are_found(db_session):
    document = Document(filename="eeff_fundacion.pdf", doc_type="pdf", status="ready", warnings=[],
                        confidentiality_tag="public")
    db_session.add(document)
    db_session.flush()
    texts = {
        "total_activo": BALANCE_ROW,
        "corriente": BALANCE_ROW.replace("Total activo'", "Total activo corriente'"),
        "note": "Financial table page 18 (6.8 Encargo fiduciario), row 'Encargo fiduciario': "
                "31/12/2024=115,321,854; 31/12/2023=101,828,343",
        "unlabelled": "Financial table page 9 (…), row 'Total activo': column 1=1; column 2=2",
    }
    for text in texts.values():
        db_session.add(Chunk(document_id=document.id, text=text, embedding=[0.0] * 1536, content_type="table_row",
                             page_number=2, confidentiality_tag="public"))
    db_session.flush()
    retrieved = [_row("some note text", str(document.id), 18)]
    try:
        found = statement_rows(db_session, ["¿Cuál fue el total de activos en 2024?", None], retrieved, "analyst", "t")
        labels = [row.text.split("row '")[1].split("'")[0] for row in found.rows]
        assert labels[0] == "Total activo"                     # the line asked about, first
        assert "Encargo fiduciario" not in labels              # not named by the question
        assert all("column 1" not in row.text for row in found.rows)  # unreadable columns left out
        assert {row.chunk_id for row in found.rows if "Total activo'" in row.text} <= found.full_match_ids
        assert not any("corriente" in row.text for row in found.rows if row.chunk_id in found.full_match_ids)
    finally:
        db_session.rollback()


# --- conflicts ------------------------------------------------------------------------------------

def test_a_total_and_its_breakdown_are_not_the_same_line():
    total = _statement("Ingresos totales 55,494,941.", _citation("Financial table page 3 (ESTADO), row 'Total Ingresos': 31/12/2024=55,494,941"))
    part = _statement("Otros ingresos 1,392,396.", _citation("Financial table page 3 (ESTADO), row 'Otros ingresos': 31/12/2024=1,392,396"))
    assert not same_line(total, part)


def test_segment_and_consolidated_lines_of_one_document_are_different():
    consolidated = _statement("Operating income 68,593.", _citation("Financial table page 48 (STATEMENTS OF OPERATIONS), row 'Operating income': 2024=68,593", page=48))
    segment = _statement("North America operating income 24,967.", _citation("Financial table page 79 (Segment Information North America), row 'Operating income': 2024=24,967", page=79))
    assert not same_line(consolidated, segment)


def test_the_same_line_in_two_documents_is_the_same_fact():
    report = _statement("Revenue 4,795,000.", _citation("Sheet 'Resumen BI' (Andina), Ingresos operacionales: 2024=4795000", "bi"))
    statement = _statement("Revenue 4,820,000.", _citation("Sheet 'Estado de resultados' (Andina), Ingresos operacionales: 2024=4820000", "eerr"))
    assert same_line(report, statement)


# --- percentages vs amounts -----------------------------------------------------------------------

def test_a_growth_percentage_does_not_support_an_amount():
    source = _citation("AWS sales increased 20% in 2025 compared to the prior year.")
    assert unverified_figures(_statement("AWS revenue in 2025 is 20 million dollars.", source)) == ["20"]
    assert unverified_figures(_statement("AWS sales grew 20% in 2025.", source)) == []


def test_a_percentage_is_supported_by_a_plain_figure_or_a_fraction():
    assert unverified_figures(_statement("Margin was 12.5% in 2024.", _citation("Margin 12.5"))) == []
    assert unverified_figures(_statement("The target gross margin is 41%.", _citation("Margen bruto objetivo: Valor=0.41"))) == []
