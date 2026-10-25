"""ID-HU-INT-002 — repository permissions and removals propagated to FinDoc.

- The mapping from a file's sharing to a classification, without a database.
- Reading permissions from Microsoft Graph and the Drive API, played by an
  httpx.MockTransport.
- The sync with a fake repository whose sharing changes between runs: files
  are really ingested and retrieved as an analyst / a controller would, so
  "I lose access" and "I gain access" are checked where they matter.
"""
import io
import uuid

import httpx
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Chunk, Document, SyncedFile, SyncSource
from app.db.session import SessionLocal
from app.retrieval.service import retrieve
from app.sync.connectors import ConnectorError, GoogleDriveConnector, RemoteFile, SharePointConnector
from app.sync.permissions import principal, resolve_access, validate_rules
from app.sync.service import sync_all

RULES = [
    {"principal": "Finance", "tag": "internal"},
    {"principal": "Legal Corporate", "tag": "restricted:legal"},
    {"principal": "Controllers", "tag": "restricted"},
    {"principal": "Compensation Committee", "tag": "restricted:compensation"},
    {"principal": "anyone", "tag": "public"},
]

# --- from sharing to a classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("shared_with", "tag"),
    [
        (["Finance"], "internal"),
        (["Legal Corporate"], "restricted:legal"),
        (["Finance", "Legal Corporate"], "internal"),          # Finance can read it: the broadest wins
        (["Controllers"], "restricted"),
        (["Legal Corporate", "Compensation Committee"], "restricted"),  # can't be one category: fail closed
        (["Only The Owner"], "restricted"),                   # no rule matches: fail closed
        (["Finance", "anyone"], "public"),
    ],
)
def test_sharing_decides_the_classification(shared_with, tag):
    assert resolve_access([principal(n) for n in shared_with], RULES)[0] == tag


def test_principals_match_by_any_name_case_insensitively():
    shared = [principal("finance@contoso.com", "FINANCE")]
    assert resolve_access(shared, RULES) == ("internal", ["Finance"])
    by_email = [{"principal": "ana@contoso.com", "tag": "restricted:legal"}]
    assert resolve_access([principal("Ana@Contoso.com", "Ana")], by_email)[0] == "restricted:legal"


def test_rules_are_validated():
    assert validate_rules([{"principal": " Legal ", "tag": "Restricted:Legal"}]) == [
        {"principal": "Legal", "tag": "restricted:legal"}
    ]
    with pytest.raises(ValueError):
        validate_rules([{"principal": "", "tag": "internal"}])
    with pytest.raises(ValueError):
        validate_rules([{"principal": "Finance", "tag": "secret"}])


# --- reading permissions ----------------------------------------------------------------------------

GRAPH = "https://graph.microsoft.com/v1.0"
REMOTE = RemoteFile(remote_id="f1", name="Legal_Contingencies_FY24.pdf", path="Legal_Contingencies_FY24.pdf",
                    version_tag="c1")


def test_sharepoint_permissions_users_groups_site_groups_and_links():
    def handler(request):
        url = str(request.url)
        if url == f"{GRAPH}/drives/d1/items/f1/permissions":
            return httpx.Response(200, json={"value": [
                {"roles": ["read"], "grantedToV2": {"siteGroup": {"displayName": "Finance Members"}}},
                {"roles": ["write"], "grantedToV2": {"user": {"email": "ana@contoso.com", "displayName": "Ana"}}},
                {"roles": ["read"], "link": {"scope": "organization"}, "grantedToIdentitiesV2": [
                    {"group": {"displayName": "Legal Corporate", "email": "legal@contoso.com"}}]},
            ], "@odata.nextLink": f"{GRAPH}/drives/d1/items/f1/permissions?page=2"})
        if url == f"{GRAPH}/drives/d1/items/f1/permissions?page=2":
            return httpx.Response(200, json={"value": [{"roles": ["read"], "link": {"scope": "anonymous"}}]})
        return httpx.Response(404, json={"error": {"message": url}})

    connector = SharePointConnector({"drive_id": "d1"}, lambda: "t", httpx.Client(transport=httpx.MockTransport(handler)))
    names = [p.names for p in connector.permissions(REMOTE)]

    assert names == [
        ("Finance Members",), ("ana@contoso.com", "Ana"), ("legal@contoso.com", "Legal Corporate"),
        ("organization",), ("anyone",),
    ]


def test_drive_permissions_users_groups_domains_and_anyone():
    def handler(request):
        assert request.url.path == "/drive/v3/files/f1/permissions"
        if request.url.params.get("pageToken") != "p2":
            return httpx.Response(200, json={"nextPageToken": "p2", "permissions": [
                {"type": "group", "emailAddress": "finance@contoso.com", "displayName": "Finance"},
                {"type": "user", "emailAddress": "old@contoso.com", "deleted": True},
            ]})
        return httpx.Response(200, json={"permissions": [
            {"type": "domain", "domain": "contoso.com"}, {"type": "anyone"},
        ]})

    connector = GoogleDriveConnector({"folder_id": "x"}, lambda: "t", httpx.Client(transport=httpx.MockTransport(handler)))
    assert [p.names for p in connector.permissions(REMOTE)] == [
        ("finance@contoso.com", "Finance"), ("domain:contoso.com",), ("anyone",),
    ]


# --- the sync ----------------------------------------------------------------------------------------

def _workbook(marker: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([f"Reporte {marker}"])
    ws.append(["Concepto", "FY24"])
    ws.append([f"Contingencias {marker}", 1_000_000])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class SharedRepository:
    """A folder's files {remote_id: (name, version_tag, bytes)} and who
    each is shared with {remote_id: [names]}."""

    def __init__(self):
        self.files: dict[str, tuple[str, str, bytes]] = {}
        self.shared_with: dict[str, list[str]] = {}
        self.unreadable: set[str] = set()

    def list_files(self):
        for remote_id, (name, tag, _) in self.files.items():
            yield RemoteFile(remote_id=remote_id, name=name, path=name, version_tag=tag)

    def download(self, remote):
        return self.files[remote.remote_id][2]

    def permissions(self, remote):
        if remote.remote_id in self.unreadable:
            raise ConnectorError("Reading the permissions failed (403): insufficientFilePermissions")
        return [principal(n) for n in self.shared_with.get(remote.remote_id, [])]


@pytest.fixture
def folder(db_session):
    """A connected folder with access rules, backed by a SharedRepository."""
    repo = SharedRepository()
    source = SyncSource(kind="sharepoint", name=f"perm-{uuid.uuid4().hex[:6]}", config={"folder_path": "x"},
                        confidentiality_tag="internal", last_summary={}, permission_rules=RULES)
    db_session.add(source)
    db_session.commit()

    def run():
        results = sync_all(SessionLocal, connector_factory=lambda *_: repo, source_ids=[source.id]) or {}
        db_session.expire_all()
        return results[str(source.id)]

    def document(remote_id):
        record = db_session.scalars(select(SyncedFile).where(
            SyncedFile.source_id == source.id, SyncedFile.remote_id == remote_id)).one()
        return record, db_session.get(Document, record.document_id)

    folder.repo, folder.source, folder.run, folder.document = repo, source, run, document
    yield folder
    db_session.rollback()
    groups = {d.version_group for _, d in (document(f.remote_id) for f in db_session.scalars(
        select(SyncedFile).where(SyncedFile.source_id == source.id))) if d is not None}
    db_session.delete(db_session.get(SyncSource, source.id))
    for group in groups:
        for version in db_session.scalars(select(Document).where(Document.version_group == group)):
            db_session.delete(version)
    db_session.commit()


def _sees(db_session, marker, role, document_id=None) -> bool:
    results = retrieve(db_session, question=f"Contingencias {marker}", top_k=5, user_roles=role,
                       user_id="perm-test", document_id=document_id)
    return any(marker in r.text for r in results)


def test_main_access_restricted_in_sharepoint_is_lost_in_findoc(db_session, folder):
    marker = f"lc{uuid.uuid4().hex[:6]}"
    folder.repo.files["f1"] = (f"Legal_Contingencies_FY24_{marker}.xlsx", "c1", _workbook(marker))
    folder.repo.shared_with["f1"] = ["Finance Members", "Finance"]
    folder.run()
    assert _sees(db_session, marker, "analyst")  # the Finance group could read it

    folder.repo.shared_with["f1"] = ["Legal Corporate"]  # restricted in SharePoint
    summary = folder.run()

    record, document = folder.document("f1")
    assert summary["permissions_changed"] == 1 and summary["unchanged"] == 0
    assert (document.confidentiality_tag, record.access_tag, record.permissions) == (
        "restricted:legal", "restricted:legal", ["Legal Corporate"])
    assert {c.confidentiality_tag for c in db_session.scalars(select(Chunk).where(Chunk.document_id == document.id))} == {
        "restricted:legal"}
    assert not _sees(db_session, marker, "analyst")                    # I lose access…
    assert not _sees(db_session, marker, "analyst", document.id)       # …even asking about it explicitly
    assert _sees(db_session, marker, "financial-controller")           # legal access still reads it


def test_alternate_2_access_opened_up_to_finance_is_granted(db_session, folder):
    marker = f"sr{uuid.uuid4().hex[:6]}"
    folder.repo.files["f1"] = (f"Segment_Report_FY24_{marker}.xlsx", "c1", _workbook(marker))
    folder.repo.shared_with["f1"] = ["Controllers"]
    folder.run()
    assert not _sees(db_session, marker, "analyst")

    folder.repo.shared_with["f1"] = ["Controllers", "Finance"]  # shared with the broader Finance group
    summary = folder.run()

    assert summary["permissions_changed"] == 1
    assert folder.document("f1")[1].confidentiality_tag == "internal"
    assert _sees(db_session, marker, "analyst")


def test_unchanged_sharing_changes_nothing(db_session, folder):
    folder.repo.files["f1"] = ("Report.xlsx", "c1", _workbook("same"))
    folder.repo.shared_with["f1"] = ["Finance"]
    folder.run()
    summary = folder.run()
    assert summary["unchanged"] == 1 and summary["permissions_changed"] == 0


def test_alternate_1_document_deleted_from_sharepoint_stops_answering(db_session, folder):
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    marker = f"db{uuid.uuid4().hex[:6]}"
    folder.repo.files["f1"] = (f"Draft_Budget_2023_{marker}.xlsx", "c1", _workbook(marker))
    folder.repo.shared_with["f1"] = ["Finance"]
    folder.run()
    _, document = folder.document("f1")
    assert _sees(db_session, marker, "analyst")

    del folder.repo.files["f1"]  # deleted from the SharePoint folder
    summary = folder.run()

    record, document = folder.document("f1")
    assert summary["removed"] == 1
    assert (record.status, document.status, document.is_current) == ("removed", "removed", False)
    assert not _sees(db_session, marker, "admin")                  # unavailable for new queries…
    assert not _sees(db_session, marker, "admin", document.id)     # …even scoped to it explicitly
    response = client.post("/ratios", json={"document_ids": [str(document.id)]})
    assert response.status_code == 410 and "removed from its repository" in response.json()["detail"]

    folder.repo.files["f1"] = (f"Draft_Budget_2023_{marker}.xlsx", "c1", _workbook(marker))  # restored
    summary = folder.run()
    assert summary["restored"] == 1
    assert folder.document("f1")[1].status == "ready" and _sees(db_session, marker, "analyst")


def test_tightening_also_applies_to_previous_versions(db_session, folder):
    old, new = f"v1{uuid.uuid4().hex[:6]}", f"v2{uuid.uuid4().hex[:6]}"
    folder.repo.files["f1"] = ("Contingencies.xlsx", "c1", _workbook(old))
    folder.repo.shared_with["f1"] = ["Finance"]
    folder.run()
    first = folder.document("f1")[1].id
    folder.repo.files["f1"] = ("Contingencies.xlsx", "c2", _workbook(new))
    folder.run()

    folder.repo.shared_with["f1"] = ["Legal Corporate"]
    folder.run()

    assert db_session.get(Document, first).confidentiality_tag == "restricted:legal"
    assert not _sees(db_session, old, "analyst", first)  # the superseded version can't be read either


def test_unreadable_permissions_leave_the_document_as_it_was(db_session, folder):
    folder.repo.files["f1"] = ("Report.xlsx", "c1", _workbook("unreadable"))
    folder.repo.shared_with["f1"] = ["Finance"]
    folder.run()
    folder.repo.unreadable.add("f1")
    folder.repo.shared_with["f1"] = ["Legal Corporate"]

    summary = folder.run()

    assert summary["failed"] == 1
    assert folder.document("f1")[1].confidentiality_tag == "internal"
    assert "insufficientFilePermissions" in db_session.get(SyncSource, folder.source.id).last_error


def test_without_rules_a_reviewers_classification_is_kept(db_session, folder):
    source = db_session.get(SyncSource, folder.source.id)
    source.permission_rules = []
    db_session.commit()
    folder.repo.files["f1"] = ("Report.xlsx", "c1", _workbook("manual"))
    folder.run()
    _, document = folder.document("f1")
    assert document.confidentiality_tag == "internal"  # the folder's fixed classification
    document.confidentiality_tag = "restricted:legal"  # reclassified by a reviewer in FinDoc
    db_session.commit()

    folder.run()

    assert folder.document("f1")[1].confidentiality_tag == "restricted:legal"


def test_access_rules_api(db_session, folder):
    from app.main import app

    client = TestClient(app)
    url = f"/sync/sources/{folder.source.id}"
    reviewer = {"X-User-Role": "reviewer"}
    assert client.patch(url, json={"permission_rules": []}).status_code == 403
    bad = client.patch(url, json={"permission_rules": [{"principal": "Finance", "tag": "secret"}]}, headers=reviewer)
    assert bad.status_code == 400
    updated = client.patch(url, headers=reviewer, json={"permission_rules": [
        {"principal": "Finance Members", "tag": "Internal"}]})
    assert updated.status_code == 200
    assert updated.json()["permission_rules"] == [{"principal": "Finance Members", "tag": "internal"}]
