"""ID-HU-INT-003 — one failed or corrupted file never blocks the rest of a
sync; failures are retried with backoff and flagged for manual review when
the retries run out.

A fake repository serves real workbooks (and a corrupted one); files are
really ingested into the test database and retrieved as an analyst would.
Retries are made due by moving a file's next_retry_at, instead of waiting.
"""
import io
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Document, SyncedFile, SyncFailure, SyncSource
from app.db.session import SessionLocal
from app.retrieval.service import retrieve
from app.sync.connectors import RemoteFile
from app.sync.retry import backoff
from app.sync.service import sync_all


def _workbook(marker: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([f"Reporte {marker}"])
    ws.append(["Concepto", "Q3 2026"])
    ws.append([f"Ingresos {marker}", 1_000])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


CORRUPTED = b"PK\x03\x04 this is not really a workbook"


class FlakyRepository:
    """Files {remote_id: (name, version_tag, bytes)}; `failing` makes a
    file's download fail (`times` more times, or always with None)."""

    def __init__(self):
        self.files: dict[str, tuple[str, str, bytes]] = {}
        self.failing: dict[str, int | None] = {}
        self.downloads: list[str] = []

    def list_files(self):
        for remote_id, (name, tag, _) in self.files.items():
            yield RemoteFile(remote_id=remote_id, name=name, path=name, version_tag=tag)

    def download(self, remote):
        self.downloads.append(remote.remote_id)
        if remote.remote_id in self.failing:
            left = self.failing[remote.remote_id]
            if left is None or left > 0:
                if left is not None:
                    self.failing[remote.remote_id] = left - 1
                raise httpx.ConnectError("Temporary failure in name resolution")
        return self.files[remote.remote_id][2]


@pytest.fixture
def folder(db_session):
    repo = FlakyRepository()
    source = SyncSource(kind="google_drive", name=f"retry-{uuid.uuid4().hex[:6]}", config={"folder_id": "x"},
                        confidentiality_tag="internal", last_summary={})
    db_session.add(source)
    db_session.commit()

    def run():
        results = sync_all(SessionLocal, connector_factory=lambda *_: repo, source_ids=[source.id]) or {}
        db_session.expire_all()
        return results[str(source.id)]

    def record(remote_id) -> SyncedFile:
        return db_session.scalars(select(SyncedFile).where(
            SyncedFile.source_id == source.id, SyncedFile.remote_id == remote_id)).one()

    def make_due(remote_id):
        r = record(remote_id)
        r.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()

    def failures(remote_id=None):
        query = select(SyncFailure).where(SyncFailure.source_id == source.id)
        if remote_id:
            query = query.where(SyncFailure.remote_id == remote_id)
        return db_session.scalars(query.order_by(SyncFailure.occurred_at)).all()

    folder.repo, folder.source, folder.run, folder.record = repo, source, run, record
    folder.make_due, folder.failures = make_due, failures
    yield folder
    db_session.rollback()
    ids = [r.document_id for r in db_session.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id))]
    db_session.delete(db_session.get(SyncSource, source.id))
    for document_id in filter(None, ids):
        document = db_session.get(Document, document_id)
        if document is not None:
            for version in db_session.scalars(select(Document).where(Document.version_group == document.version_group)):
                db_session.delete(version)
    db_session.commit()


def test_backoff_doubles_up_to_the_cap():
    assert [backoff(n) for n in (1, 2, 3, 4)] == [timedelta(minutes=m) for m in (15, 30, 60, 120)]
    assert backoff(20) == timedelta(minutes=1440)


def test_main_one_corrupted_file_out_of_twenty_doesnt_block_the_rest(db_session, folder):
    tag = uuid.uuid4().hex[:6]
    for i in range(19):
        folder.repo.files[f"f{i}"] = (f"Report_{tag}_{i}.xlsx", "v1", _workbook(f"ok{tag}x{i}"))
    folder.repo.files["bad"] = (f"Corrupted_{tag}.xlsx", "v1", CORRUPTED)
    documents_before = db_session.scalar(select(func.count()).select_from(Document))

    summary = folder.run()

    assert (summary["new"], summary["failed"]) == (19, 1)
    for i in range(19):  # the other 19 are ingested and an analyst can query them
        document = db_session.get(Document, folder.record(f"f{i}").document_id)
        assert (document.status, document.is_current) == ("ready", True)
        hits = retrieve(db_session, question=f"Ingresos ok{tag}x{i}", top_k=3, user_roles="analyst",
                        user_id="t", document_id=document.id)
        assert any(f"ok{tag}x{i}" in h.text for h in hits)
    bad = folder.record("bad")
    assert (bad.status, bad.attempts, bad.document_id) == ("failed", 1, None)
    [logged] = folder.failures("bad")
    assert (logged.stage, logged.attempt, logged.needs_review, logged.resolved_at) == ("ingestion", 1, False, None)
    assert logged.next_retry_at - logged.occurred_at == timedelta(minutes=15)  # a retry is scheduled
    # The corrupted copy isn't left behind as a failed document.
    assert db_session.scalar(select(func.count()).select_from(Document)) == documents_before + 19


def test_a_failed_file_waits_for_its_retry(db_session, folder):
    folder.repo.files["f1"] = ("Report.xlsx", "v1", _workbook("wait"))
    folder.repo.failing["f1"] = None
    folder.run()

    summary = folder.run()  # before its retry is due

    assert summary["retry_pending"] == 1
    assert folder.repo.downloads == ["f1"]  # not attempted again yet


def test_alternate_1_a_temporary_failure_succeeds_on_retry(db_session, folder):
    marker = f"tmp{uuid.uuid4().hex[:6]}"
    folder.repo.files["f1"] = (f"Aug_Report_{marker}.xlsx", "v1", _workbook(marker))
    folder.repo.failing["f1"] = 1  # one connectivity hiccup
    assert folder.run()["failed"] == 1

    folder.make_due("f1")
    summary = folder.run()

    assert summary["new"] == 1
    record = folder.record("f1")
    assert (record.status, record.attempts, record.next_retry_at, record.error) == ("ingested", 0, None, None)
    [logged] = folder.failures("f1")
    assert logged.stage == "download" and "name resolution" in logged.error
    assert logged.resolution == "retry succeeded" and logged.resolved_at is not None  # the log reflects it
    hits = retrieve(db_session, question=f"Ingresos {marker}", top_k=3, user_roles="analyst", user_id="t")
    assert any(marker in h.text for h in hits)


def test_alternate_2_exhausted_retries_flag_the_file_for_review(db_session, folder, caplog):
    folder.repo.files["f1"] = ("Board_Pack.xlsx", "v1", _workbook("never"))
    folder.repo.failing["f1"] = None  # fails every time
    folder.run()
    for _ in range(3):
        folder.make_due("f1")
        assert folder.run()["failed"] == 1

    folder.make_due("f1")
    with caplog.at_level(logging.ERROR, logger="app.sync.retry"):
        summary = folder.run()  # the fifth and final attempt

    assert summary["needs_review"] == 1
    record = folder.record("f1")
    assert (record.status, record.attempts, record.next_retry_at) == ("needs_review", 5, None)
    logged = folder.failures("f1")
    assert [f.attempt for f in logged] == [1, 2, 3, 4, 5]
    assert logged[-1].needs_review and not any(f.needs_review for f in logged[:-1])
    alerts = [r for r in caplog.records if r.levelno == logging.ERROR and "flagged for manual review" in r.getMessage()]
    assert len(alerts) == 1  # the administrator alert

    # No more automatic retries, even long after.
    record.next_retry_at = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.commit()
    downloads = len(folder.repo.downloads)
    assert folder.run()["needs_review"] == 1
    assert len(folder.repo.downloads) == downloads


def test_a_flagged_file_that_changes_at_the_source_is_tried_again(db_session, folder):
    folder.repo.files["f1"] = ("Board_Pack.xlsx", "v1", CORRUPTED)
    folder.run()
    record = folder.record("f1")
    record.status, record.attempts = "needs_review", 5
    db_session.commit()

    folder.repo.files["f1"] = ("Board_Pack.xlsx", "v2", _workbook("fixed"))  # someone fixed it
    summary = folder.run()

    assert summary["new"] == 1
    assert folder.record("f1").status == "ingested"


def test_a_failed_file_that_leaves_the_folder_resolves_its_failures(db_session, folder):
    folder.repo.files["f1"] = ("Gone.xlsx", "v1", CORRUPTED)
    folder.run()
    del folder.repo.files["f1"]
    folder.run()
    [logged] = folder.failures("f1")
    assert logged.resolution == "left the folder"


# --- API ------------------------------------------------------------------------------------------

def test_failure_log_and_retry_api(db_session, folder, monkeypatch):
    import app.api.routes_sync as routes
    from app.main import app

    client = TestClient(app)
    reviewer = {"X-User-Role": "reviewer"}
    folder.repo.files["f1"] = ("Board_Pack.xlsx", "v1", _workbook("api"))
    folder.repo.failing["f1"] = None
    folder.run()

    assert client.get("/sync/failures").status_code == 403
    log = client.get("/sync/failures", params={"source_id": str(folder.source.id), "unresolved": True},
                     headers=reviewer).json()
    assert [(f["path"], f["stage"], f["attempt"], f["source_name"]) for f in log] == [
        ("Board_Pack.xlsx", "download", 1, folder.source.name)]
    overview = client.get("/sync/sources", headers=reviewer).json()
    assert overview["open_failures"] >= 1 and overview["retry_max_attempts"] == 5
    files = client.get(f"/sync/sources/{folder.source.id}/files", headers=reviewer).json()
    assert files[0]["attempts"] == 1 and files[0]["next_retry_at"]

    record = folder.record("f1")
    record.status, record.attempts = "needs_review", 5
    db_session.commit()
    started = threading.Event()
    monkeypatch.setattr(routes, "sync_all", lambda *a, **k: started.set())
    response = client.post(f"/sync/files/{record.id}/retry", headers=reviewer)
    assert response.status_code == 202 and started.wait(2)
    db_session.expire_all()
    assert (folder.record("f1").status, folder.record("f1").attempts) == ("failed", 0)

    folder.repo.failing.clear()
    folder.run()
    ingested = folder.record("f1")
    assert client.post(f"/sync/files/{ingested.id}/retry", headers=reviewer).status_code == 400
