"""ID-HU-BE-007: relevant source retrieval (integration, needs Postgres/pgvector).

Covers the story's scenarios: ranked retrieval across several documents
(income statement + cash flow statement), restricted content excluded for a
user without the role, and no sources at all when nothing is relevant.

The fake bag-of-words embedding (conftest) scores by shared words, so these
tests pass `min_score` explicitly instead of relying on RETRIEVAL_MIN_SCORE,
which is calibrated for real embeddings.
"""
import pytest

from app.db.models import Chunk, Document
import app.retrieval.service as retrieval_service
from app.qa.query_expansion import retrieve_for_question
from app.retrieval.service import restricted_match_exists, retrieve

MIN_SCORE = 0.25

INCOME_STATEMENT = [
    "zq1 income statement revenue 500000",
    "zq1 income statement operating expenses 320000",
    "zq1 income statement net income 120000",
]
CASH_FLOW_STATEMENT = [
    "zq1 cash flow statement starting point net income 120000",
    "zq1 cash flow statement depreciation 15000",
    "zq1 cash flow statement net cash from operating activities 140000",
]
COMPENSATION = ["zq1 executive compensation bonus pool 90000"]


@pytest.fixture
def statements(db_session):
    """Q1 income statement and Q1 cash flow statement (public), plus a
    restricted compensation note; removed afterwards."""
    # The same (fake) provider retrieval uses, patched in by conftest.
    provider = retrieval_service.get_embedding_provider()
    documents = {}
    for filename, texts, tag in [
        ("zq1_income_statement.xlsx", INCOME_STATEMENT, "public"),
        ("zq1_cash_flow_statement.pdf", CASH_FLOW_STATEMENT, "public"),
        ("zq1_compensation.xlsx", COMPENSATION, "restricted:compensation"),
    ]:
        document = Document(filename=filename, doc_type="excel", status="ready", warnings=[],
                            confidentiality_tag=tag)
        db_session.add(document)
        db_session.flush()
        db_session.add_all([
            Chunk(document_id=document.id, text=text, embedding=embedding, confidentiality_tag=tag)
            for text, embedding in zip(texts, provider.embed(texts))
        ])
        documents[filename] = document
    db_session.commit()
    yield documents
    for document in documents.values():
        db_session.delete(document)
    db_session.commit()


def test_results_are_ranked_by_relevance_with_a_score(db_session, statements):
    results = retrieve(db_session, "zq1 net income", top_k=8, min_score=MIN_SCORE)

    assert results
    scores = [r.score for r in results]
    assert all(s is not None and MIN_SCORE <= s <= 1 for s in scores)
    assert scores == sorted(scores, reverse=True)
    assert "net income" in results[0].text


def test_question_spanning_two_statements_retrieves_from_both(db_session, statements):
    question = "Does zq1 net income reconcile with the starting point of the cash flow statement?"

    results = retrieve(db_session, question, top_k=4, min_score=MIN_SCORE)

    filenames = {r.document_filename for r in results}
    assert {"zq1_income_statement.xlsx", "zq1_cash_flow_statement.pdf"} <= filenames
    texts = " | ".join(r.text for r in results)
    assert "income statement net income 120000" in texts
    assert "starting point net income 120000" in texts


def test_qa_retrieval_keeps_both_statements_when_one_has_more_matches(db_session, statements):
    """The QA path (diversified) doesn't let the larger match fill every slot."""
    question = "zq1 cash flow statement net income"

    results = retrieve_for_question(db_session, question, top_k=3)

    filenames = {r.document_filename for r in results}
    assert {"zq1_income_statement.xlsx", "zq1_cash_flow_statement.pdf"} <= filenames


def test_restricted_passage_is_excluded_without_the_role(db_session, statements):
    question = "zq1 executive compensation bonus pool"

    analyst = retrieve(db_session, question, top_k=8, user_roles="analyst", min_score=MIN_SCORE)
    cleared = retrieve(db_session, question, top_k=8, user_roles="compensation-access", min_score=MIN_SCORE)

    assert all("compensation" not in r.text for r in analyst)
    assert any("compensation" in r.text for r in cleared)
    # The analyst is told something relevant was withheld, without seeing it.
    assert restricted_match_exists(db_session, question, top_k=8, user_roles="analyst", min_score=MIN_SCORE)


def test_unrelated_question_does_not_trigger_the_restricted_notice(db_session, statements):
    assert not restricted_match_exists(
        db_session, "supplier concentration risk", top_k=8, user_roles="analyst", min_score=MIN_SCORE
    )


def test_no_relevant_source_returns_nothing(db_session, statements):
    results = retrieve(db_session, "What is our supplier concentration risk?", top_k=8, min_score=MIN_SCORE)

    assert results == []


def test_relevance_floor_can_be_disabled(db_session, statements):
    """min_score=0 keeps the plain nearest-neighbour behaviour (used by figure
    extraction, where the document is already chosen)."""
    results = retrieve(db_session, "What is our supplier concentration risk?", top_k=3, min_score=0)

    assert len(results) == 3
