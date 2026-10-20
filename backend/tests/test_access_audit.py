"""ID-HU-BE-014: denied access attempts are logged, insider information is
protected, and administrators can filter the audit trail.

Denials are committed in their own session (app/audit.py), so they outlive
the test's session: every test uses its own user ids and filters by them.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit import audit_scope
from app.db.models import Chunk, Document, RetrievalAuditLog
from app.db.session import SessionLocal
from app.embeddings.factory import get_embedding_provider
from app.retrieval.access import can_access, denial_reason, is_insider, normalize_tag
from app.retrieval.service import retrieve


def _user(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _logs(**filters) -> list[RetrievalAuditLog]:
    session = SessionLocal()
    try:
        stmt = select(RetrievalAuditLog).filter_by(**filters).order_by(RetrievalAuditLog.occurred_at)
        return list(session.scalars(stmt))
    finally:
        session.close()


@pytest.fixture
def make_document(db_session):
    """A ready document with one chunk per text, all tagged `tag`."""
    created: list[Document] = []
    provider = get_embedding_provider()

    def make(filename: str, tag: str, *texts: str, storage_path: str | None = None) -> Document:
        document = Document(filename=filename, doc_type="pdf", status="ready", warnings=[],
                            confidentiality_tag=tag, storage_path=storage_path)
        db_session.add(document)
        db_session.flush()
        for text in texts:
            db_session.add(Chunk(document_id=document.id, text=text, embedding=provider.embed([text])[0],
                                 confidentiality_tag=tag, page_number=1))
        db_session.commit()
        created.append(document)
        return document

    yield make
    for document in created:
        db_session.delete(document)
    db_session.commit()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


# --- access policy --------------------------------------------------------------

def test_insider_tag_and_aliases():
    assert normalize_tag("Pre-release: Insider") == "restricted:insider"
    assert normalize_tag("insider") == "restricted:insider"
    assert is_insider("restricted:insider") is True
    assert is_insider("restricted:compensation") is False
    assert "pre-release disclosure group" in denial_reason("restricted:insider")
    assert denial_reason("restricted:compensation") == "Requires compensation-access."


def test_insider_needs_explicit_clearance():
    tag = "restricted:insider"
    assert can_access(tag, {"analyst"}) is False
    # The generic restricted clearance isn't enough for pre-release information.
    assert can_access(tag, {"restricted-reviewer"}) is False
    assert can_access(tag, {"restricted-access"}) is False
    assert can_access(tag, {"financial-controller"}) is False
    assert can_access(tag, {"pre-release-disclosure"}) is True
    assert can_access(tag, {"insider-access"}) is True
    assert can_access(tag, {"admin"}) is True
    # Other restricted tiers are unchanged.
    assert can_access("restricted:compensation", {"restricted-reviewer"}) is True


# --- main use case: a denial is logged ---------------------------------------------

def test_denied_section_is_logged_with_user_document_tier_and_time(db_session, make_document):
    document = make_document("Compensation_FY24.pdf", "restricted:compensation",
                             "executive compensation bonus was 50000")
    user = _user("analyst")
    before = datetime.now(timezone.utc)

    results = retrieve(db_session, "What was the executive compensation bonus?", top_k=5,
                       user_roles="analyst", user_id=user)

    assert all(r.document_id != str(document.id) for r in results)
    [event] = _logs(user_id=user, document_id=document.id)
    assert event.authorized is False
    assert event.document_filename == "Compensation_FY24.pdf"
    assert event.chunk_id is not None
    assert event.confidentiality_tag == "restricted:compensation"
    assert event.roles == ["analyst"]
    assert event.question == "What was the executive compensation bonus?"
    assert event.reason == "Requires compensation-access."
    assert event.occurred_at >= before - timedelta(seconds=5)
    assert event.is_insider is False


def test_authorized_user_is_logged_as_granted_not_denied(db_session, make_document):
    document = make_document("Compensation_FY24_ok.pdf", "restricted:compensation",
                             "executive compensation bonus was 50000")
    user = _user("controller")

    results = retrieve(db_session, "executive compensation bonus", top_k=5,
                       user_roles="compensation-access", user_id=user)
    db_session.commit()

    assert any(r.document_id == str(document.id) for r in results)
    events = _logs(user_id=user, document_id=document.id)
    assert events and all(e.authorized for e in events)
    assert events[0].document_filename == "Compensation_FY24_ok.pdf"


def test_unrelated_question_logs_no_denial(db_session, make_document):
    document = make_document("Compensation_FY24_unrelated.pdf", "restricted:compensation",
                             "executive compensation bonus was 50000")
    user = _user("analyst")

    retrieve(db_session, "supplier concentration risk", top_k=5, user_roles="analyst", user_id=user,
             min_score=0.25)

    assert _logs(user_id=user, document_id=document.id) == []


def test_denial_survives_a_rolled_back_request(db_session, make_document):
    document = make_document("Compensation_FY24_rollback.pdf", "restricted:compensation",
                             "executive compensation bonus was 50000")
    user = _user("analyst")

    retrieve(db_session, "executive compensation bonus", top_k=5, user_roles="analyst", user_id=user)
    db_session.rollback()  # e.g. the model service failed afterwards

    assert len(_logs(user_id=user, document_id=document.id, authorized=False)) == 1


def test_repeated_denial_in_one_request_is_logged_once(db_session, make_document):
    document = make_document("Compensation_FY24_dedupe.pdf", "restricted:compensation",
                             "executive compensation bonus was 50000")
    user = _user("analyst")

    with audit_scope("chat") as scope:
        # The bilingual search runs retrieval twice for one question.
        retrieve(db_session, "executive compensation bonus", top_k=5, user_roles="analyst", user_id=user)
        retrieve(db_session, "executive compensation bonus", top_k=5, user_roles="analyst", user_id=user)

    [event] = _logs(user_id=user, document_id=document.id)
    assert event.resource == "chat"
    assert event.request_id == scope.request_id


# --- alternate case 1: pre-release insider information ---------------------------------

@pytest.mark.parametrize("role", ["analyst", "restricted-reviewer", "financial-controller"])
def test_insider_results_are_not_disclosed_and_the_attempt_is_logged(db_session, make_document, role):
    document = make_document("Q3_Results_PreRelease.pdf", "restricted:insider",
                             "Q3 results revenue 9100000 net income 1200000")
    user = _user(role)

    results = retrieve(db_session, "What were the Q3 results revenue?", top_k=5, user_roles=role, user_id=user)

    assert all("9100000" not in r.text for r in results)
    [event] = _logs(user_id=user, document_id=document.id)
    assert event.authorized is False
    assert event.is_insider is True
    assert event.confidentiality_tag == "restricted:insider"
    assert "pre-release disclosure group" in event.reason


@pytest.mark.parametrize("role", ["pre-release-disclosure", "admin"])
def test_disclosure_group_and_admin_see_insider_information_on_record(db_session, make_document, role):
    document = make_document("Q3_Results_PreRelease_ok.pdf", "restricted:insider",
                             "Q3 results revenue 9100000 net income 1200000")
    user = _user(role)

    results = retrieve(db_session, "Q3 results revenue", top_k=5, user_roles=role, user_id=user)
    db_session.commit()

    assert any(r.document_id == str(document.id) for r in results)
    events = _logs(user_id=user, document_id=document.id)
    assert events and all(e.authorized and e.is_insider for e in events)


def test_query_endpoint_logs_insider_denial_with_request_context(client, make_document):
    document = make_document("Q3_Results_PreRelease_api.pdf", "restricted:insider",
                             "Q3 results revenue 9100000 net income 1200000")
    user = _user("analyst")

    response = client.post("/query", json={"question": "Q3 results revenue"},
                           headers={"X-User-Role": "analyst", "X-User-Id": user})

    assert response.status_code == 200
    assert all("9100000" not in r["text"] for r in response.json()["results"])
    [event] = _logs(user_id=user, document_id=document.id)
    assert event.is_insider is True
    assert event.resource == "query"
    assert event.request_id


def test_ratio_extraction_from_an_insider_document_is_logged(client, make_document):
    document = make_document("Q3_Results_PreRelease_ratios.pdf", "restricted:insider",
                             "Q3 results revenue 9100000 net income 1200000")
    user = _user("analyst")

    response = client.post("/ratios", json={"document_ids": [str(document.id)], "ratios": ["net_margin"]},
                           headers={"X-User-Role": "analyst", "X-User-Id": user})

    assert response.status_code == 200
    [ratio] = response.json()["ratios"]
    assert ratio["status"] == "unavailable" and ratio["value"] is None
    events = _logs(user_id=user, document_id=document.id)
    assert events and all(not e.authorized and e.is_insider for e in events)
    # Looked up in worker threads, still tied to the request.
    assert {e.resource for e in events} == {"ratios"}
    assert len({e.request_id for e in events}) == 1 and events[0].request_id
    assert {e.action for e in events} == {"extract"}


# --- denied documents: source panel and original file -------------------------------------

def test_opening_a_restricted_source_is_logged_without_a_chunk(client, make_document):
    document = make_document("Legal_Contingencies_FY24_open.pdf", "restricted:legal-contingencies",
                             "pending litigation provision 300000")
    user = _user("analyst")
    headers = {"X-User-Role": "analyst", "X-User-Id": user}

    resolved = client.get(f"/documents/{document.id}/resolve", headers=headers)
    cell = client.get(f"/documents/{document.id}/cells/Sheet1/B2", headers=headers)
    original = client.get(f"/documents/{document.id}/file?role=analyst&user={user}")

    assert resolved.status_code == 200 and resolved.json()["access"]["allowed"] is False
    assert cell.status_code == 403
    assert original.status_code == 403
    events = _logs(user_id=user, document_id=document.id)
    assert {(e.action, e.resource) for e in events} == {
        ("resolve", "resolve"), ("view_cell", "cells"), ("view_file", "file"),
    }
    assert all(not e.authorized and e.chunk_id is None and e.document_id == document.id for e in events)
    assert all(e.confidentiality_tag == "restricted:legal-contingencies" for e in events)


# --- alternate case 2: the administrator's audit trail ----------------------------------------

@pytest.fixture
def legal_trail(db_session, make_document):
    """Granted and denied attempts on a legal-contingencies document by two users."""
    filename = f"Legal_Contingencies_FY24_{uuid.uuid4().hex[:6]}.pdf"
    document = make_document(filename, "restricted:legal-contingencies", "pending litigation provision 300000")
    analyst, controller = _user("analyst"), _user("controller")
    retrieve(db_session, "pending litigation provision", top_k=5, user_roles="analyst", user_id=analyst)
    retrieve(db_session, "pending litigation provision", top_k=5, user_roles="financial-controller",
             user_id=controller)
    db_session.commit()
    return document, filename, analyst, controller


ADMIN = {"X-User-Role": "admin", "X-User-Id": "admin-1"}


def test_admin_filters_the_trail_by_document(client, legal_trail):
    document, filename, analyst, controller = legal_trail

    page = client.get("/audit/access-logs", params={"document": filename}, headers=ADMIN).json()

    outcomes = {(item["user_id"], item["authorized"]) for item in page["items"]}
    assert outcomes == {(analyst, False), (controller, True)}
    assert page["total"] == 2
    assert all(item["occurred_at"] and item["document_filename"] == filename for item in page["items"])

    by_id = client.get("/audit/access-logs", params={"document_id": str(document.id)}, headers=ADMIN).json()
    assert by_id["total"] == 2


def test_admin_filters_by_user_outcome_and_time(client, legal_trail):
    _, filename, analyst, _ = legal_trail
    now = datetime.now(timezone.utc)

    mine = client.get("/audit/access-logs", params={"user_id": analyst}, headers=ADMIN).json()
    assert mine["total"] == 1 and mine["items"][0]["authorized"] is False

    denied = client.get("/audit/access-logs", params={"document": filename, "authorized": "false"},
                        headers=ADMIN).json()
    assert [item["user_id"] for item in denied["items"]] == [analyst]

    window = {"document": filename, "from": (now - timedelta(minutes=5)).isoformat(),
              "to": (now + timedelta(minutes=5)).isoformat()}
    assert client.get("/audit/access-logs", params=window, headers=ADMIN).json()["total"] == 2
    past = {"document": filename, "to": (now - timedelta(days=1)).isoformat()}
    assert client.get("/audit/access-logs", params=past, headers=ADMIN).json()["total"] == 0


def test_audit_trail_pages(client, legal_trail):
    _, filename, _, _ = legal_trail

    first = client.get("/audit/access-logs", params={"document": filename, "limit": 1}, headers=ADMIN).json()
    second = client.get("/audit/access-logs", params={"document": filename, "limit": 1, "offset": 1},
                        headers=ADMIN).json()

    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_audit_trail_csv_export(client, legal_trail):
    _, filename, analyst, _ = legal_trail

    response = client.get("/audit/access-logs/export.csv", params={"document": filename}, headers=ADMIN)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("occurred_at,user_id,roles,outcome")
    assert len(lines) == 3
    assert any(analyst in line and ",denied," in line for line in lines)


def test_only_admins_can_read_the_trail_and_attempts_are_logged(client):
    user = _user("analyst")

    response = client.get("/audit/access-logs", headers={"X-User-Role": "analyst", "X-User-Id": user})
    export = client.get("/audit/access-logs/export.csv",
                        headers={"X-User-Role": "restricted-reviewer", "X-User-Id": user})

    assert response.status_code == 403 and export.status_code == 403
    events = _logs(user_id=user)
    assert len(events) == 2
    assert all(not e.authorized and e.action == "audit_query" and e.resource == "audit" for e in events)


# --- log completeness ---------------------------------------------------------------------

def test_every_denial_has_user_time_document_and_tier(db_session, client, make_document):
    document = make_document("Compensation_FY24_complete.pdf", "restricted:compensation",
                             "executive compensation bonus was 50000")
    user = _user("analyst")
    retrieve(db_session, "executive compensation bonus", top_k=5, user_roles="analyst", user_id=user)
    client.get(f"/documents/{document.id}/file", headers={"X-User-Role": "analyst", "X-User-Id": user})

    events = _logs(user_id=user, document_id=document.id, authorized=False)
    assert len(events) == 2
    for event in events:
        assert event.user_id == user
        assert event.occurred_at is not None
        assert event.document_id == document.id and event.document_filename
        assert event.confidentiality_tag == "restricted:compensation"
        assert event.reason
