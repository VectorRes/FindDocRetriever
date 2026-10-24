"""ID-HU-BE-016 — source-of-truth tier prioritization and draft flagging.

The tier rules, the set-aside of lower-tier sources and the answer's notices
run without a database. The end-to-end tests ingest real workbooks (fake
embeddings, see conftest.py) and script the chat model, so retrieval,
annotation, prioritization and the notices run as they do for /query/answer.
"""
import re
import uuid

import openpyxl
import pytest
from fastapi.testclient import TestClient

import app.qa.graph as qa_graph_module
from app.db.models import Document
from app.ingestion.service import ingest_excel_file
from app.qa.schemas import (
    GroundedAnswer, GroundedStatement, LLMCitation, LLMGroundedAnswer, LLMMetricSource, LLMStatement, LLMTriage,
    ResolvedCitation,
)
from app.qa.service import answer_question
from app.qa.source_hierarchy import SetAside, set_aside_lower_tiers, source_notices
from app.retrieval.service import RetrievedChunk
from app.source_tiers import ERP_EPM, INTERNAL_REPORT, OFFICIAL_REPORT, infer_tier, tier_rank
from app.versioning import approve

# --- tiers ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "tier"),
    [
        ("SAP_export_Andina_Q2_2026.xlsx", ERP_EPM),
        ("EPM_Andina_2025.xlsx", ERP_EPM),
        ("Hyperion-Consolidado-2025.xlsx", ERP_EPM),
        ("ERP Andina junio 2026.xlsx", ERP_EPM),
        ("10-Q-Q3-2026-as-filed.pdf", OFFICIAL_REPORT),
        ("Annual_Report_2025.pdf", OFFICIAL_REPORT),
        ("Estados_financieros_auditados_2024.pdf", OFFICIAL_REPORT),
        ("2026q2-alphabet-earnings-release.pdf", OFFICIAL_REPORT),
        ("Reporte_Gerencial_Andina_Q2_2026.xlsx", INTERNAL_REPORT),
        ("EERR_Andina_2024.xlsx", INTERNAL_REPORT),
        ("Forecast_Andina_2026_draft.xlsx", INTERNAL_REPORT),
        ("Sapphire_budget.xlsx", INTERNAL_REPORT),  # "sap" only as a whole word
    ],
)
def test_tier_is_inferred_from_the_file_name(filename, tier):
    assert infer_tier(filename) == tier


def test_tiers_rank_erp_first():
    assert tier_rank(ERP_EPM) < tier_rank(OFFICIAL_REPORT) < tier_rank(INTERNAL_REPORT)
    assert tier_rank(None) == tier_rank(INTERNAL_REPORT)


# --- prioritization -------------------------------------------------------------------------------

def _chunk(filename, tier, text="Ingresos: Q2 2026=1,250,000", approved=True, document_id=None):
    return RetrievedChunk(
        document_id=document_id or filename, document_filename=filename, sheet_name="Hoja1", cell_range="A2:B2",
        page_number=None, text=text, source_tier=tier, approved=approved,
    )


def _triage(*entries):
    return LLMTriage(
        requested_metric="revenue", metric_in_sources=True,
        metric_sources=[LLMMetricSource(source_id=sid, entity=entity, period=period) for sid, entity, period in entries],
    )


def test_alternate_2_internal_report_is_set_aside_for_erp_data():
    sources = {"S1": _chunk("SAP_Andina_Q2_2026.xlsx", ERP_EPM), "S2": _chunk("Reporte_Gerencial_Andina.xlsx", INTERNAL_REPORT)}

    kept, set_aside = set_aside_lower_tiers(_triage(("S1", "Andina", "Q2 2026"), ("S2", "Andina S.A.S.", "Q2 2026")), sources)

    assert list(kept) == ["S1"]
    assert [(s.source.document_filename, s.preferred.document_filename) for s in set_aside] == [
        ("Reporte_Gerencial_Andina.xlsx", "SAP_Andina_Q2_2026.xlsx")
    ]


@pytest.mark.parametrize(
    "entries",
    [
        (("S1", "Andina", "Q2 2026"), ("S2", "Andina", "Q1 2026")),     # different periods
        (("S1", "Andina", "Q2 2026"), ("S2", "Pacifico", "Q2 2026")),   # different companies
        (("S1", "Andina", None), ("S2", "Andina", "Q2 2026")),          # period unknown
        (("S1", "Andina", "Q2 2026"),),                                 # only one gives the figure
    ],
)
def test_sources_that_arent_the_same_figure_are_kept(entries):
    sources = {"S1": _chunk("SAP_Andina.xlsx", ERP_EPM), "S2": _chunk("Reporte_Gerencial_Andina.xlsx", INTERNAL_REPORT)}
    kept, set_aside = set_aside_lower_tiers(_triage(*entries), sources)
    assert list(kept) == ["S1", "S2"] and set_aside == []


def test_entity_left_unsaid_by_one_source_still_matches():
    sources = {"S1": _chunk("10-Q-2026.pdf", OFFICIAL_REPORT), "S2": _chunk("Reporte_Gerencial.xlsx", INTERNAL_REPORT)}
    kept, _ = set_aside_lower_tiers(_triage(("S1", None, "Q2 2026"), ("S2", "Andina", "Q2 2026")), sources)
    assert list(kept) == ["S1"]


# --- notices --------------------------------------------------------------------------------------

def _answer(*chunks):
    statements = [
        GroundedStatement(text="Revenue for Q2 2026 was 1,250,000.", citations=[ResolvedCitation(
            source_id=f"S{i}", document_id=c.document_id, document_filename=c.document_filename,
            sheet_name=c.sheet_name, cell_range=c.cell_range, page_number=None, reference_number=None, text=c.text,
        )])
        for i, c in enumerate(chunks, start=1)
    ]
    return GroundedAnswer(question="q", statements=statements, grounded=True)


def _notices(chunks, set_aside=()):
    return source_notices(_answer(*chunks), {c.document_id: c for c in chunks}, list(set_aside))


def test_main_internal_report_used_without_erp_says_it_isnt_reconciled():
    report = _chunk("Reporte_Gerencial_Andina_Q2_2026.xlsx", INTERNAL_REPORT)
    [notice] = _notices([report])
    assert notice.kind == "lower_tier"
    assert notice.document_filenames == ["Reporte_Gerencial_Andina_Q2_2026.xlsx"]
    assert "No ERP/EPM source covering this was available" in notice.message
    assert "an internal report" in notice.message
    assert "haven't been reconciled against the ERP/EPM source of truth" in notice.message


def test_alternate_1_draft_forecast_is_flagged():
    forecast = _chunk("Forecast_Andina_2026.xlsx", INTERNAL_REPORT, approved=False)
    notices = {n.kind: n for n in _notices([forecast])}
    assert notices["draft"].document_filenames == ["Forecast_Andina_2026.xlsx"]
    assert "draft document not yet approved" in notices["draft"].message
    assert "provisional, not approved" in notices["draft"].message


def test_approved_erp_source_needs_no_notice():
    assert _notices([_chunk("SAP_Andina.xlsx", ERP_EPM)]) == []


def test_draft_erp_source_is_still_flagged_as_draft():
    assert [n.kind for n in _notices([_chunk("SAP_Andina.xlsx", ERP_EPM, approved=False)])] == ["draft"]


def test_part_of_the_answer_from_a_lower_tier_is_noted():
    erp, report = _chunk("SAP_Andina.xlsx", ERP_EPM), _chunk("Reporte_Gerencial.xlsx", INTERNAL_REPORT)
    [notice] = _notices([erp, report])
    assert notice.kind == "lower_tier" and notice.message.startswith("Part of this answer comes from an internal report")


def test_alternate_2_set_aside_source_is_noted_as_not_primary():
    erp, report = _chunk("SAP_Andina.xlsx", ERP_EPM), _chunk("Reporte_Gerencial.xlsx", INTERNAL_REPORT)
    notices = source_notices(_answer(erp), {c.document_id: c for c in (erp, report)}, [SetAside(report, erp)])
    [notice] = notices
    assert notice.kind == "higher_tier_preferred"
    assert notice.message == (
        "Reporte_Gerencial.xlsx (internal report) also covers this figure but wasn't used as the primary source: "
        "SAP_Andina.xlsx (ERP/EPM) ranks higher."
    )


def test_no_notices_without_an_answer():
    report = _chunk("Reporte.xlsx", INTERNAL_REPORT, approved=False)
    assert source_notices(GroundedAnswer(question="q"), {report.document_id: report}, []) == []


# --- end to end: retrieval -> prioritization -> answer -> notices ---------------------------------

class _ScriptedModel:
    """Triage says every source of this test's workbooks (named by `tag`)
    gives Q2 2026 revenue for one company; the answer cites the first such
    source it's shown. Records the prompts."""

    def __init__(self, tag):
        self.tag = tag
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        return _Structured(self, schema)


class _Structured:
    def __init__(self, model, schema):
        self.model, self.schema = model, schema

    def invoke(self, messages):
        prompt = messages[-1]["content"]
        self.model.prompts.append(prompt)
        # Each source: "[S1] (location) text" (triage) or "[S1] (location)\ntext" (generation).
        blocks = dict(re.findall(r"^\[(S\d+)\](.*?)(?=^\[S\d+\]|\Z)", prompt, re.MULTILINE | re.DOTALL))
        ids = [sid for sid, block in blocks.items() if self.model.tag in block.lower()]
        if self.schema is LLMTriage:
            return LLMTriage(
                requested_metric="revenue", metric_in_sources=True, question_specifies_entity=True,
                question_specifies_period=True,
                metric_sources=[LLMMetricSource(source_id=sid, entity="Boreal", period="Q2 2026") for sid in ids],
            )
        first = ids[0]
        amount = int(re.search(r"Q2 2026=(\d+)", blocks[first]).group(1))
        return LLMGroundedAnswer(statements=[LLMStatement(
            text=f"Revenue for Q2 2026 was {amount:,}.", citations=[LLMCitation(source_id=first)],
        )])


@pytest.fixture
def workbooks(db_session, tmp_path):
    tag = uuid.uuid4().hex[:6]
    created = []

    def add(filename, revenue):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Ingresos"
        ws.append([f"Boreal {tag} ingresos trimestre"])
        ws.append(["Concepto", "Q2 2026"])
        ws.append([f"Ingresos boreal{tag}", revenue])
        path = tmp_path / f"{uuid.uuid4().hex}.xlsx"
        wb.save(path)
        document = ingest_excel_file(db_session, filename=filename.format(tag=tag), file_path=path)
        document.confidentiality_tag = "internal"
        for chunk in document.chunks:
            chunk.confidentiality_tag = "internal"
        db_session.commit()
        created.append(document)
        return document

    add.tag = tag
    yield add
    for document in created:
        db_session.delete(document)
    db_session.commit()


def _ask(db_session, monkeypatch, tag):
    model = _ScriptedModel(tag)
    monkeypatch.setattr(qa_graph_module, "get_chat_model", lambda: model)
    answer = answer_question(db_session, f"Ingresos boreal{tag} Q2 2026", top_k=5, user_roles="analyst")
    return answer, model


def test_e2e_alternate_2_answers_from_erp_and_notes_the_internal_report(db_session, monkeypatch, workbooks):
    report = workbooks("Reporte_Gerencial_Boreal_{tag}_Q2_2026.xlsx", 1_250_000)
    erp = workbooks("SAP_Boreal_{tag}_Q2_2026.xlsx", 1_250_000)
    approve(db_session, erp)
    approve(db_session, report)
    db_session.commit()
    assert (erp.source_tier, report.source_tier) == (ERP_EPM, INTERNAL_REPORT)

    answer, model = _ask(db_session, monkeypatch, workbooks.tag)

    generation_prompt = model.prompts[-1]
    assert erp.filename in generation_prompt and "tier: ERP/EPM" in generation_prompt
    assert report.filename not in generation_prompt  # set aside before the answer was written
    assert {c.document_filename for s in answer.statements for c in s.citations} == {erp.filename}
    assert [(n.kind, n.document_filenames) for n in answer.source_notices] == [
        ("higher_tier_preferred", [report.filename])
    ]
    assert [v.source_tier for v in answer.versions_used] == [ERP_EPM]


def test_e2e_main_internal_report_used_when_the_erp_export_isnt_ingested(db_session, monkeypatch, workbooks):
    report = workbooks("Reporte_Gerencial_Boreal_{tag}_Q2_2026.xlsx", 1_250_000)
    approve(db_session, report)
    db_session.commit()

    answer, _ = _ask(db_session, monkeypatch, workbooks.tag)

    assert {c.document_filename for s in answer.statements for c in s.citations} == {report.filename}
    [notice] = answer.source_notices
    assert notice.kind == "lower_tier"
    assert "haven't been reconciled against the ERP/EPM source of truth" in notice.message


def test_e2e_alternate_1_only_a_draft_forecast(db_session, monkeypatch, workbooks):
    forecast = workbooks("Forecast_Boreal_{tag}_Q2_2026.xlsx", 1_300_000)
    assert forecast.approval_status == "draft"

    answer, _ = _ask(db_session, monkeypatch, workbooks.tag)

    kinds = {n.kind: n for n in answer.source_notices}
    assert kinds["draft"].document_filenames == [forecast.filename]
    assert "lower_tier" in kinds


# --- API ------------------------------------------------------------------------------------------

@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_source_tier_endpoint(client, db_session, workbooks):
    report = workbooks("Reporte_Gerencial_Boreal_{tag}.xlsx", 1)
    url = f"/documents/{report.id}/source-tier"

    assert client.patch(url, json={"tier": ERP_EPM}, headers={"X-User-Role": "analyst"}).status_code == 403
    assert client.patch(url, json={"tier": "gossip"}, headers={"X-User-Role": "reviewer"}).status_code == 400
    response = client.patch(url, json={"tier": ERP_EPM}, headers={"X-User-Role": "reviewer"})
    assert response.status_code == 200 and response.json()["source_tier"] == ERP_EPM
    db_session.expire_all()
    assert db_session.get(Document, report.id).source_tier == ERP_EPM
    listed = next(d for d in client.get("/documents").json()["documents"] if d["id"] == str(report.id))
    assert listed["source_tier"] == ERP_EPM
