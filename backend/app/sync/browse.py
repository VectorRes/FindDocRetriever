"""Folder browser for a signed-in account (ID-HU-INT-001): the Repositories
tab walks it to pick the folders to sync.

Node ids are opaque to the frontend; `None` is the top level:
  - Google:    "My Drive" (root), "Shared with me", and each shared drive;
               then folders by their Drive id.
  - Microsoft: "My OneDrive" and "SharePoint sites" -> each site -> its
               document libraries -> folders ("item|<drive>|<item>").
A node that can be connected carries the `config` a sync source needs.
"""
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx

from app.sync.connectors import DRIVE, GOOGLE_FOLDER, GRAPH, _check

MAX_ITEMS = 500


@dataclass
class FolderNode:
    id: str
    name: str
    # Can be connected as a sync source, with this config.
    selectable: bool = False
    config: dict = field(default_factory=dict)
    # Can be opened to see the folders inside.
    browsable: bool = True


def _headers(token: Callable[[], str]) -> dict:
    return {"Authorization": f"Bearer {token()}"}


# --- Google Drive -------------------------------------------------------------------------------

def _drive_folders(client: httpx.Client, token, q: str) -> Iterator[dict]:
    params = {
        "q": q, "fields": "nextPageToken, files(id, name)", "pageSize": "200", "orderBy": "name",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "allDrives",
    }
    seen = 0
    while seen < MAX_ITEMS:
        body = _check(client.get(f"{DRIVE}/files", params=params, headers=_headers(token)),
                      "Listing Google Drive folders").json()
        for item in body.get("files", []):
            seen += 1
            yield item
        if not body.get("nextPageToken"):
            return
        params["pageToken"] = body["nextPageToken"]


def browse_google(client: httpx.Client, token: Callable[[], str], parent: str | None) -> list[FolderNode]:
    folder = f"mimeType = '{GOOGLE_FOLDER}' and trashed = false"
    if parent is None:
        nodes = [
            FolderNode("root", "My Drive", selectable=True, config={"folder_id": "root"}),
            FolderNode("shared", "Shared with me"),
        ]
        drives = _check(client.get(f"{DRIVE}/drives", params={"pageSize": "100"}, headers=_headers(token)),
                        "Listing shared drives").json()
        nodes += [FolderNode(d["id"], d["name"], selectable=True, config={"folder_id": d["id"]})
                  for d in drives.get("drives", [])]
        return nodes
    query = f"sharedWithMe and {folder}" if parent == "shared" else f"'{parent}' in parents and {folder}"
    return [FolderNode(f["id"], f["name"], selectable=True, config={"folder_id": f["id"]})
            for f in _drive_folders(client, token, query)]


# --- Microsoft (OneDrive / SharePoint) ----------------------------------------------------------

def _graph_items(client: httpx.Client, token, url: str, params: dict | None = None) -> Iterator[dict]:
    seen = 0
    while url and seen < MAX_ITEMS:
        body = _check(client.get(url, params=params, headers=_headers(token)), "Browsing Microsoft 365").json()
        for item in body.get("value", []):
            seen += 1
            yield item
        url, params = body.get("@odata.nextLink"), None


def _graph_folders(client, token, drive_id: str, url: str) -> list[FolderNode]:
    return [
        FolderNode(f"item|{drive_id}|{item['id']}", item["name"], selectable=True,
                   config={"drive_id": drive_id, "item_id": item["id"]})
        for item in _graph_items(client, token, url, {"$select": "id,name,folder", "$top": "200"})
        if "folder" in item
    ]


def browse_microsoft(client: httpx.Client, token: Callable[[], str], parent: str | None) -> list[FolderNode]:
    if parent is None:
        return [FolderNode("me", "My OneDrive"), FolderNode("sites", "SharePoint sites")]
    if parent == "sites":
        sites = _graph_items(client, token, f"{GRAPH}/sites", {"search": "*", "$top": "200"})
        return sorted(
            (FolderNode(f"site|{s['id']}", s.get("displayName") or s.get("name") or s.get("webUrl", "?"))
             for s in sites),
            key=lambda node: node.name.lower(),
        )
    if parent.startswith("site|"):
        site_id = parent.split("|", 1)[1]
        return [
            FolderNode(f"drive|{d['id']}", d.get("name", "Documents"), selectable=True, config={"drive_id": d["id"]})
            for d in _graph_items(client, token, f"{GRAPH}/sites/{site_id}/drives")
        ]
    if parent == "me":
        drive = _check(client.get(f"{GRAPH}/me/drive", headers=_headers(token)), "Opening OneDrive").json()
        return _graph_folders(client, token, drive["id"], f"{GRAPH}/drives/{drive['id']}/root/children")
    if parent.startswith("drive|"):
        drive_id = parent.split("|", 1)[1]
        return _graph_folders(client, token, drive_id, f"{GRAPH}/drives/{drive_id}/root/children")
    if parent.startswith("item|"):
        _, drive_id, item_id = parent.split("|", 2)
        return _graph_folders(client, token, drive_id, f"{GRAPH}/drives/{drive_id}/items/{item_id}/children")
    raise ValueError(f"Unknown folder: {parent}")


def browse(provider: str, client: httpx.Client, token: Callable[[], str], parent: str | None) -> list[FolderNode]:
    return (browse_google if provider == "google" else browse_microsoft)(client, token, parent)
