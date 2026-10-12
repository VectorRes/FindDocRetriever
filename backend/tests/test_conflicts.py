"""Which "conflicting" flags from the model survive (app/qa/graph.py::settle_conflicts)."""
import pytest
from app.qa.graph import settle_conflicts
from app.qa.schemas import GroundedStatement, ResolvedCitation


def _statement(text, filename, sheet, source_text, conflicting=True):
    citation = ResolvedCitation("S1", "d", filename, sheet, "A7:C7", None, None, source_text)
    return GroundedStatement(text=text, citations=[citation], conflicting=conflicting)


BUDGET = ("Presupuesto_Andina_2025.xlsx", "Proyección", "Utilidad neta: 2025 presupuesto=723723 (formula: =C5-C6)")
ACTUAL_2025 = ("EERR_Andina_2025.xlsx", "Estado de resultados", "Utilidad neta: 2025=702000")
BI = ("Reporte_BI_Andina_2024.xlsx", "Resumen BI", "Ingresos operacionales: 2024=4795000")
ERP = ("EERR_Andina_2024.xlsx", "Estado de resultados", "Ingresos operacionales: 2024=4820000")


def test_budget_vs_actual_is_not_a_conflict():
    budget = _statement("Budgeted net income 2025 is 723,723", *BUDGET)
    actual = _statement("Net income 2025 is 702,000", *ACTUAL_2025)

    settle_conflicts([budget, actual])

    assert not budget.conflicting and not actual.conflicting


def test_a_lone_conflicting_statement_is_unflagged():
    budget = _statement("Budgeted net income 2025 is 723,723", *BUDGET, conflicting=False)
    actual = _statement("Net income 2025 is 702,000 per another document", *ACTUAL_2025)

    settle_conflicts([budget, actual])

    assert not actual.conflicting


def test_two_actual_figures_that_disagree_stay_a_conflict():
    bi = _statement("Revenue 2024 is 4,795,000", *BI)
    erp = _statement("Revenue 2024 is 4,820,000", *ERP)

    settle_conflicts([bi, erp])

    assert bi.conflicting and erp.conflicting


def test_two_budgets_that_disagree_stay_a_conflict():
    first = _statement("Budgeted net income 2025 is 723,723", *BUDGET)
    second = _statement("Budgeted net income 2025 is 700,000", "Budget_v2.xlsx", "Plan", "Net income: 700000")

    settle_conflicts([first, second])

    assert first.conflicting and second.conflicting


def test_statements_not_flagged_are_untouched():
    plain = _statement("Net income 2024 is 610,000", *ERP, conflicting=False)

    settle_conflicts([plain])

    assert not plain.conflicting


# --- conflicts detected from triage's per-source facts -----------------------------------

from app.qa.graph import clean_statement_text, detect_conflicts  # noqa: E402
from app.qa.schemas import LLMMetricSource, LLMTriage  # noqa: E402


def _cited(text, source_id, filename, sheet, source_text):
    citation = ResolvedCitation(source_id, "d", filename, sheet, "A5:B5", None, None, source_text)
    return GroundedStatement(text=text, citations=[citation])


def _facts(*entries):
    return LLMTriage(metric_in_sources=True, metric_sources=[
        LLMMetricSource(source_id=sid, entity=entity, period=period) for sid, entity, period in entries
    ])


def test_same_entity_and_period_with_different_amounts_is_flagged():
    erp = _cited("Revenue for Andina in 2024 is 4,820,000 COP.", "S2", *ERP)
    bi = _cited("Revenue for Andina in 2024 is 4,795,000 COP.", "S6", *BI)
    triage = _facts(("S2", "Andina S.A.S.", "2024"), ("S6", "Andina", "2024"))

    detect_conflicts([erp, bi], triage, {})

    assert erp.conflicting and bi.conflicting


def test_different_periods_or_matching_amounts_are_not_flagged():
    y2024 = _cited("Revenue for Andina in 2024 is 4,820,000 COP.", "S2", *ERP)
    y2025 = _cited("Revenue for Andina in 2025 is 5,310,000 COP.", "S5", "EERR_Andina_2025.xlsx", "Estado de resultados", "2025=5310000")
    same = _cited("Revenue for Andina in 2024 is 4,820,000 COP.", "S7", "Resumen.xlsx", "Resumen", "2024=4820000")
    triage = _facts(("S2", "Andina", "2024"), ("S5", "Andina", "2025"), ("S7", "Andina", "2024"))

    detect_conflicts([y2024, y2025, same], triage, {})

    assert not any(s.conflicting for s in (y2024, y2025, same))


def test_budget_and_actual_for_the_same_period_are_not_flagged():
    budget = _cited("Budgeted net income 2025 is 723,723.", "S1", *BUDGET)
    actual = _cited("Net income 2025 is 702,000.", "S4", *ACTUAL_2025)
    triage = _facts(("S1", "Andina", "2025"), ("S4", "Andina", "2025"))

    detect_conflicts([budget, actual], triage, {})

    assert not budget.conflicting and not actual.conflicting


def test_sources_outside_the_requested_metric_are_ignored():
    revenue = _cited("Revenue 2024 is 4,820,000.", "S2", *ERP)
    tax = _cited("Income tax 2024 is 233,500.", "S8", "EERR_Andina_2024.xlsx", "Estado de resultados", "2024=-233500")

    detect_conflicts([revenue, tax], _facts(("S2", "Andina", "2024")), {})

    assert not revenue.conflicting and not tax.conflicting


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("Operational revenue for Andina S.A.S. in 2024 is 4,820,000 COP (S2).},{",
      "Operational revenue for Andina S.A.S. in 2024 is 4,820,000 COP."),
     ("Net income is 610,000 [S1, S3].", "Net income is 610,000."),
     ('The line is labelled "Net sales".', 'The line is labelled "Net sales".'),
     ("Budgeted net income is operating income minus tax (=C5-C6).",
      "Budgeted net income is operating income minus tax (=C5-C6).")],
)
def test_clean_statement_text(raw, clean):
    assert clean_statement_text(raw) == clean


def test_the_same_figure_in_another_currency_is_not_a_conflict():
    usd = _cited("Revenue for Andina in 2024 is 1,205 dollars (USD).", "S1", "Resumen_USD.xlsx", "Summary",
                 "(Cifras en dólares estadounidenses (USD)), Revenue: 2024=1205")
    erp = _cited("Revenue for Andina in 2024 is 4,820,000 pesos colombianos (COP).", "S2", *ERP)
    bi = _cited("Revenue for Andina in 2024 is 4,795,000 pesos colombianos (COP).", "S6", *BI)
    triage = _facts(("S1", "Andina", "2024"), ("S2", "Andina", "2024"), ("S6", "Andina", "2024"))

    detect_conflicts([usd, erp, bi], triage, {})

    assert not usd.conflicting
    assert erp.conflicting and bi.conflicting


def test_model_flag_across_currencies_alone_is_dropped():
    usd = _cited("Revenue 2024 is 1,205 USD.", "S1", "Resumen_USD.xlsx", "Summary", "Revenue: 2024=1205")
    cop = _cited("Revenue 2024 is 4,820,000 COP.", "S2", *ERP)
    usd.conflicting = cop.conflicting = True

    settle_conflicts([usd, cop])

    assert not usd.conflicting and not cop.conflicting


def test_conflicting_sources_make_the_answer_low_confidence():
    from app.qa.confidence import assess_confidence

    confidence = assess_confidence(kept_count=2, proposed_count=2, removed_figures=[], self_confidence="high",
                                   cited_scores=[], has_conflicts=True)

    assert confidence.level == "low"
    assert any("disagree" in reason for reason in confidence.reasons)


ROW_TWO_YEARS = ("alphabet.pdf", None,
                 "Financial table page 6, row 'Revenues': Quarter Ended June 30 2025=96,428; Quarter Ended June 30 2026=119,796")


def test_years_stated_by_each_statement_beat_triages_single_period_per_source():
    # Triage tagged the row "2026", but it holds both years.
    y2025 = _cited("Total revenues for 2025 are 96,428.", "S6", *ROW_TWO_YEARS)
    y2026 = _cited("Total revenues for 2026 are 119,796.", "S6", *ROW_TWO_YEARS)
    triage = _facts(("S6", "Alphabet", "2026"))

    detect_conflicts([y2025, y2026], triage, {})

    assert not y2025.conflicting and not y2026.conflicting


def test_without_a_stated_year_a_multi_year_source_gives_no_period():
    a = _cited("Total revenues are 96,428.", "S6", *ROW_TWO_YEARS)
    b = _cited("Total revenues are 119,796.", "S6", *ROW_TWO_YEARS)

    detect_conflicts([a, b], _facts(("S6", "Alphabet", "2026")), {})

    assert not a.conflicting and not b.conflicting


def test_quarters_of_one_year_are_different_periods():
    q1 = _cited("Revenue for Q1 2026 is 109,896.", "S2", "a.pdf", None, "Q1 2026=109,896")
    q2 = _cited("Revenue for Q2 2026 is 119,796.", "S3", "a.pdf", None, "Q2 2026=119,796")

    detect_conflicts([q1, q2], _facts(("S2", "Alphabet", "2026"), ("S3", "Alphabet", "2026")), {})

    assert not q1.conflicting and not q2.conflicting
