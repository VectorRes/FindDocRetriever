"""ID-HU-INT-001 — automatic sync of SharePoint and Google Drive folders.

- Connectors: Microsoft Graph and the Drive API are played by an
  httpx.MockTransport (sign-in, site and library lookup, pagination,
  subfolders, downloads, Google Sheets export), no real accounts.
- Sync: a fake repository stands in for the connector; files really are
  ingested into the test database (fake embeddings, see conftest.py) and
  then retrieved, as an analyst's question would.
"""
import io
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models import Document, SyncedFile, SyncSource
from app.db.session import SessionLocal, engine
from app.retrieval.service import retrieve
from app.sync.connectors import (
    ConnectorError, GoogleDriveConnector, GraphToken, RemoteFile, SharePointConnector, folder_id_from,
)
from app.sync.scheduler import SyncScheduler
from app.sync.service import SYNC_LOCK_KEY, sync_all

# --- SharePoint connector (Microsoft Graph) -----------------------------------------------------

GRAPH = "https://graph.microsoft.com/v1.0"


def _graph_transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append((request.method, url, request.headers.get("Authorization")))
        if "login.microsoftonline.com/tenant-1/oauth2/v2.0/token" in url:
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "graph-token", "expires_in": 3600})
        if url == f"{GRAPH}/sites/contoso.sharepoint.com:/sites/Finance":
            return httpx.Response(200, json={"id": "site-1"})
        if url == f"{GRAPH}/sites/site-1/drives":
            return httpx.Response(200, json={"value": [
                {"id": "drive-other", "name": "Archive", "webUrl": "https://contoso.sharepoint.com/sites/Finance/Archive"},
                {"id": "drive-1", "name": "Documents",
                 "webUrl": "https://contoso.sharepoint.com/sites/Finance/Shared%20Documents"},
            ]})
        if url.startswith(f"{GRAPH}/drives/drive-1/root:/Corporate%20Finance/Q3%20Reports:/children"):
            return httpx.Response(200, json={
                "value": [
                    {"id": "f1", "name": "Q3_Consolidated_Report.xlsx", "file": {}, "cTag": "c1",
                     "eTag": "e1", "lastModifiedDateTime": "2026-10-01T10:00:00Z", "size": 100},
                    {"id": "dir1", "name": "Archive", "folder": {"childCount": 1}},
                ],
                "@odata.nextLink": f"{GRAPH}/drives/drive-1/root/next-page?token=abc",
            })
        if url == f"{GRAPH}/drives/drive-1/root/next-page?token=abc":
            return httpx.Response(200, json={"value": [
                {"id": "f2", "name": "Board_Notes.docx", "file": {}, "cTag": "c2"},
            ]})
        if url.startswith(f"{GRAPH}/drives/drive-1/items/dir1/children"):
            return httpx.Response(200, json={"value": [
                {"id": "f3", "name": "Q2_Report.pdf", "file": {}, "cTag": "c3"},
            ]})
        if url == f"{GRAPH}/drives/drive-1/items/f1/content":
            return httpx.Response(200, content=b"xlsx-bytes")
        return httpx.Response(404, json={"error": {"message": f"no route {url}"}})

    return httpx.MockTransport(handler)


def _sharepoint(calls, **config):
    client = httpx.Client(transport=_graph_transport(calls))
    token = GraphToken("tenant-1", "client-1", "secret-1", client)
    return SharePointConnector({
        "site_url": "https://contoso.sharepoint.com/sites/Finance", "library": "Documents",
        "folder_path": "Corporate Finance/Q3 Reports", **config,
    }, token, client)


def test_sharepoint_lists_the_folder_with_content_tags_and_pages():
    calls = []
    files = list(_sharepoint(calls).list_files())

    assert [(f.remote_id, f.name, f.version_tag) for f in files] == [
        ("f1", "Q3_Consolidated_Report.xlsx", "c1"), ("f2", "Board_Notes.docx", "c2"),
    ]
    assert files[0].modified_at == datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
    graph_calls = [c for c in calls if c[1].startswith(GRAPH)]
    assert all(auth == "Bearer graph-token" for _, _, auth in graph_calls)
    assert sum("oauth2" in url for _, url, _ in calls) == 1  # the token is reused


def test_sharepoint_subfolders_and_download():
    calls = []
    connector = _sharepoint(calls, include_subfolders=True)
    files = list(connector.list_files())
    assert [f.path for f in files] == ["Q3_Consolidated_Report.xlsx", "Board_Notes.docx", "Archive/Q2_Report.pdf"]
    assert connector.download(files[0]) == b"xlsx-bytes"


def test_sharepoint_unknown_library_is_explained():
    connector = _sharepoint([], library="Finanzas")
    with pytest.raises(ConnectorError, match="no document library 'Finanzas'.*Archive, Documents"):
        list(connector.list_files())


def test_sharepoint_sign_in_failure_is_a_connector_error():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": "invalid_client"})
    ))
    token = GraphToken("tenant-1", "client-1", "wrong", client)
    with pytest.raises(ConnectorError, match="SharePoint sign-in failed \\(401\\): invalid_client"):
        token()


# --- Google Drive connector ----------------------------------------------------------------------

DRIVE = "https://www.googleapis.com/drive/v3"


def _drive_transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("Authorization")))
        params = request.url.params
        if request.url.path == "/drive/v3/files":
            assert params["supportsAllDrives"] == "true"
            if params["q"] == "'folder-1' in parents and trashed = false":
                if params.get("pageToken") != "p2":
                    return httpx.Response(200, json={"nextPageToken": "p2", "files": [
                        {"id": "g1", "name": "Aug_Management_Report.xlsx", "mimeType": "application/octet-stream",
                         "md5Checksum": "md5-1", "modifiedTime": "2026-09-01T08:00:00.000Z", "size": "2048"},
                        {"id": "sub", "name": "Old", "mimeType": "application/vnd.google-apps.folder"},
                    ]})
                return httpx.Response(200, json={"files": [
                    {"id": "g2", "name": "Forecast", "mimeType": "application/vnd.google-apps.spreadsheet",
                     "version": "7", "modifiedTime": "2026-09-02T08:00:00.000Z"},
                    {"id": "g3", "name": "Minutes", "mimeType": "application/vnd.google-apps.document"},
                ]})
            if params["q"] == "'sub' in parents and trashed = false":
                return httpx.Response(200, json={"files": [
                    {"id": "g4", "name": "July.pdf", "mimeType": "application/pdf", "md5Checksum": "md5-4"},
                ]})
        if request.url.path == "/drive/v3/files/g1" and params.get("alt") == "media":
            return httpx.Response(200, content=b"binary-xlsx")
        if request.url.path == "/drive/v3/files/g2/export":
            assert params["mimeType"].endswith("spreadsheetml.sheet")
            return httpx.Response(200, content=b"exported-xlsx")
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.MockTransport(handler)


def _drive(calls, **config):
    return GoogleDriveConnector({"folder_id": "folder-1", **config}, lambda: "drive-token",
                                httpx.Client(transport=_drive_transport(calls)))


def test_drive_lists_files_across_pages_with_checksums():
    calls = []
    files = list(_drive(calls).list_files())

    assert [(f.remote_id, f.name, f.version_tag) for f in files] == [
        ("g1", "Aug_Management_Report.xlsx", "md5-1"),
        ("g2", "Forecast.xlsx", "v7:2026-09-02T08:00:00.000Z"),  # a Google Sheet, exported as .xlsx
    ]
    assert files[0].size == 2048
    assert all(auth == "Bearer drive-token" for _, auth in calls)


def test_drive_subfolders_downloads_and_sheet_export():
    connector = _drive([], include_subfolders=True)
    files = {f.remote_id: f for f in connector.list_files()}
    assert files["g4"].path == "Old/July.pdf"
    assert connector.download(files["g1"]) == b"binary-xlsx"
    assert connector.download(files["g2"]) == b"exported-xlsx"


def test_drive_folder_can_be_given_by_url():
    assert folder_id_from("https://drive.google.com/drive/folders/1AbC-d_9?usp=sharing") == "1AbC-d_9"
    assert folder_id_from("1AbC-d_9") == "1AbC-d_9"
    with pytest.raises(ConnectorError):
        GoogleDriveConnector({"folder_id": ""}, lambda: "t", httpx.Client())


# --- the sync ------------------------------------------------------------------------------------

def _workbook(marker: str, value: int) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resultados"
    ws.append([f"Reporte {marker}"])
    ws.append(["Concepto", "Q3 2026"])
    ws.append([f"Ingresos {marker}", value])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class FakeRepository:
    """A connected folder's files: {remote_id: (name, version_tag, bytes)}."""

    def __init__(self):
        self.files: dict[str, tuple[str, str, bytes]] = {}
        self.downloads: list[str] = []
        self.fail_downloads: set[str] = set()
        self.unreachable = False

    def list_files(self):
        if self.unreachable:
            raise ConnectorError("SharePoint sign-in failed (401): invalid_client")
        for remote_id, (name, tag, _) in self.files.items():
            yield RemoteFile(remote_id=remote_id, name=name, path=f"Q3 Reports/{name}", version_tag=tag)

    def download(self, remote):
        self.downloads.append(remote.remote_id)
        if remote.remote_id in self.fail_downloads:
            raise ConnectorError("Downloading failed (503): service unavailable")
        return self.files[remote.remote_id][2]


@pytest.fixture
def repos(db_session):
    """Connected sources backed by fake repositories, cleaned up afterwards."""
    created: list[SyncSource] = []
    repositories: dict[str, FakeRepository] = {}

    def connect(kind="sharepoint", **settings):
        source = SyncSource(kind=kind, name=f"{kind}-{uuid.uuid4().hex[:6]}", config={"folder_path": "x"},
                            confidentiality_tag=settings.pop("confidentiality_tag", "internal"),
                            last_summary={}, **settings)
        db_session.add(source)
        db_session.commit()
        created.append(source)
        repositories[str(source.id)] = FakeRepository()
        return source, repositories[str(source.id)]

    def run(source):
        """Sync `source` from its own fake repository."""
        repository = repositories[str(source.id)]
        results = sync_all(SessionLocal, connector_factory=lambda *_: repository, source_ids=[source.id]) or {}
        db_session.expire_all()
        return results

    connect.run = run
    connect.repositories = repositories
    yield connect
    db_session.rollback()
    for source in created:
        documents = [f.document_id for f in db_session.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id))]
        db_session.delete(db_session.get(SyncSource, source.id))
        for document_id in documents:
            document = db_session.get(Document, document_id) if document_id else None
            if document is not None:
                for version in db_session.scalars(select(Document).where(Document.version_group == document.version_group)):
                    db_session.delete(version)
        db_session.commit()


def _found(db_session, question, marker):
    results = retrieve(db_session, question=question, top_k=3, user_roles="analyst", user_id="sync-test")
    return [r for r in results if marker in r.text]


def test_main_new_sharepoint_file_is_ingested_and_queryable(db_session, repos):
    source, repo = repos("sharepoint")
    marker = f"zq{uuid.uuid4().hex[:6]}"
    repo.files["f1"] = (f"Q3_Consolidated_Report_{marker}.xlsx", "c1", _workbook(marker, 1_250_000))

    [summary] = repos.run(source).values()

    assert summary == {"new": 1, "updated": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    record = db_session.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id)).one()
    document = db_session.get(Document, record.document_id)
    assert (record.status, record.version_tag, document.status) == ("ingested", "c1", "ready")
    assert document.confidentiality_tag == "internal" and document.storage_path
    hits = _found(db_session, f"Ingresos {marker}", marker)
    assert hits and hits[0].document_filename == document.filename  # an analyst can query it right away
    refreshed = db_session.get(SyncSource, source.id)
    assert refreshed.last_status == "ok" and refreshed.last_sync_at is not None


def test_unchanged_files_are_not_downloaded_again(db_session, repos):
    source, repo = repos("sharepoint")
    repo.files["f1"] = ("Q3_Report.xlsx", "c1", _workbook("unchanged", 1))
    repos.run(source)

    [summary] = repos.run(source).values()

    assert summary["unchanged"] == 1 and summary["new"] == 0
    assert repo.downloads == ["f1"]


def test_alternate_1_new_google_drive_file_is_ingested(db_session, repos):
    source, repo = repos("google_drive")
    marker = f"gd{uuid.uuid4().hex[:6]}"
    repo.files["g1"] = (f"Aug_Management_Report_{marker}.xlsx", "md5-1", _workbook(marker, 980_000))

    [summary] = repos.run(source).values()

    assert summary["new"] == 1
    assert _found(db_session, f"Ingresos {marker}", marker)


def test_alternate_2_edited_file_is_reingested_as_a_new_version(db_session, repos):
    source, repo = repos("sharepoint")
    old, new = f"old{uuid.uuid4().hex[:6]}", f"new{uuid.uuid4().hex[:6]}"
    repo.files["f1"] = (f"Q3_Consolidated_Report_{old}.xlsx", "c1", _workbook(old, 1_000))
    repos.run(source)
    first = db_session.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id)).one().document_id

    repo.files["f1"] = (repo.files["f1"][0], "c2", _workbook(new, 2_000))  # edited and saved in place
    [summary] = repos.run(source).values()

    assert summary["updated"] == 1
    record = db_session.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id)).one()
    previous, current = db_session.get(Document, first), db_session.get(Document, record.document_id)
    assert current.id != previous.id and current.version_group == previous.version_group
    assert (current.version_number, current.is_current, previous.is_current) == (2, True, False)
    assert record.version_tag == "c2"
    assert _found(db_session, f"Ingresos {new}", new)
    assert not _found(db_session, f"Ingresos {old}", old)  # the stale copy no longer answers


def test_criterion_3_one_run_checks_both_repositories(db_session, repos, monkeypatch):
    sharepoint, sp_repo = repos("sharepoint")
    drive, gd_repo = repos("google_drive")
    sp_repo.files["f1"] = ("SP_Report.xlsx", "c1", _workbook("sp", 1))
    gd_repo.files["g1"] = ("GD_Report.xlsx", "m1", _workbook("gd", 2))
    by_kind = {"sharepoint": sp_repo, "google_drive": gd_repo}
    seen = []

    def factory(kind, config, client):
        seen.append(kind)
        return by_kind[kind]

    results = sync_all(SessionLocal, connector_factory=factory, source_ids=[sharepoint.id, drive.id])

    assert sorted(seen) == ["google_drive", "sharepoint"]
    assert {sid: r["new"] for sid, r in results.items()} == {str(sharepoint.id): 1, str(drive.id): 1}


def test_a_failed_file_is_recorded_and_retried(db_session, repos):
    source, repo = repos("sharepoint")
    repo.files["f1"] = ("Q3_Report.xlsx", "c1", _workbook("retry", 1))
    repo.fail_downloads.add("f1")

    [summary] = repos.run(source).values()
    record = db_session.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id)).one()
    assert summary["failed"] == 1 and record.status == "failed" and "503" in record.error
    assert db_session.get(SyncSource, source.id).last_status == "error"

    repo.fail_downloads.clear()
    [summary] = repos.run(source).values()
    assert summary["new"] == 1
    db_session.refresh(record)
    assert record.status == "ingested" and record.error is None


def test_other_file_types_are_skipped(db_session, repos):
    source, repo = repos("sharepoint")
    repo.files["f1"] = ("Board_Notes.docx", "c1", b"docx")
    [summary] = repos.run(source).values()
    assert summary["skipped"] == 1 and repo.downloads == []


def test_an_unreachable_repository_is_reported(db_session, repos):
    source, repo = repos("sharepoint")
    repo.unreachable = True
    repos.run(source)
    refreshed = db_session.get(SyncSource, source.id)
    assert refreshed.last_status == "error" and "invalid_client" in refreshed.last_error


def test_auto_approve_makes_an_update_current_right_away(db_session, repos):
    source, repo = repos("sharepoint", auto_approve=True, source_tier="erp_epm", confidentiality_tag="public")
    repo.files["f1"] = ("SAP_Export.xlsx", "c1", _workbook("ap1", 1))
    repos.run(source)
    repo.files["f1"] = ("SAP_Export.xlsx", "c2", _workbook("ap2", 2))
    repos.run(source)

    record = db_session.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id)).one()
    current = db_session.get(Document, record.document_id)
    assert (current.approval_status, current.is_current, current.version_number) == ("approved", True, 2)
    assert (current.source_tier, current.confidentiality_tag) == ("erp_epm", "public")


def test_a_sync_already_running_is_not_doubled(repos):
    source, _ = repos("sharepoint")
    with engine.connect() as other:
        assert other.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": SYNC_LOCK_KEY})
        try:
            assert sync_all(SessionLocal, connector_factory=lambda *a: None, source_ids=[source.id]) is None
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SYNC_LOCK_KEY})
            other.commit()


# --- the scheduler -------------------------------------------------------------------------------

def test_scheduler_runs_every_interval_until_stopped():
    runs = []
    done = threading.Event()

    def run():
        runs.append(time.monotonic())
        if len(runs) >= 3:
            done.set()

    scheduler = SyncScheduler(run, interval_seconds=0.05, first_delay_seconds=0)
    scheduler.start()
    try:
        assert done.wait(2), "the scheduler didn't run three times"
    finally:
        scheduler.stop()
    count = len(runs)
    time.sleep(0.15)
    assert len(runs) == count  # stopped
    assert scheduler.next_run_at is not None


def test_scheduler_keeps_going_after_a_failed_run():
    calls = []
    done = threading.Event()

    def run():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("repository down")
        done.set()

    scheduler = SyncScheduler(run, interval_seconds=0.05, first_delay_seconds=0)
    scheduler.start()
    try:
        assert done.wait(2)
    finally:
        scheduler.stop()


def test_interval_zero_never_starts():
    scheduler = SyncScheduler(lambda: None, interval_seconds=0)
    scheduler.start()
    assert scheduler._thread is None


# --- API ------------------------------------------------------------------------------------------

@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_sync_api(client, db_session, monkeypatch):
    import app.api.routes_sync as routes

    reviewer = {"X-User-Role": "reviewer"}
    assert client.get("/sync/sources").status_code == 403
    bad = client.post("/sync/sources", json={"kind": "sharepoint", "name": "x", "config": {}}, headers=reviewer)
    assert bad.status_code == 400
    bad_drive = client.post("/sync/sources", json={"kind": "google_drive", "name": "x", "config": {}}, headers=reviewer)
    assert bad_drive.status_code == 400

    created = client.post("/sync/sources", headers=reviewer, json={
        "kind": "google_drive", "name": "Finance/Management Reports",
        "config": {"folder_id": "https://drive.google.com/drive/folders/1AbC?usp=sharing"},
    })
    assert created.status_code == 201
    source = created.json()
    try:
        assert source["config"]["folder_id"] == "1AbC" and source["confidentiality_tag"] == "internal"
        overview = client.get("/sync/sources", headers=reviewer).json()
        assert any(s["id"] == source["id"] for s in overview["sources"])
        assert overview["interval_minutes"] == 0  # off in tests (conftest)
        assert client.get(f"/sync/sources/{source['id']}/files", headers=reviewer).json() == []

        started = threading.Event()
        monkeypatch.setattr(routes, "sync_all", lambda *a, **k: started.set())
        response = client.post("/sync/run", headers=reviewer)
        assert response.status_code == 202 and response.json() == {"started": True}
        assert started.wait(2)
    finally:
        assert client.delete(f"/sync/sources/{source['id']}", headers=reviewer).status_code == 204
    assert client.get(f"/sync/sources/{source['id']}/files", headers=reviewer).status_code == 404
