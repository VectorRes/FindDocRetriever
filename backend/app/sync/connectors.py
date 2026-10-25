"""Repository connectors (ID-HU-INT-001): list a folder's files with a
version tag, and download one.

- SharePoint, through Microsoft Graph: an Entra ID app authenticates with the
  client-credentials flow; the folder is a path inside a document library of
  a site. The version tag is the item's cTag, which changes only when the
  content changes (a rename doesn't re-ingest the file).
- Google Drive, through the Drive API v3: a service account the folder is
  shared with authenticates with a signed JWT (google-auth). The version tag
  is the file's md5 checksum; native Google Sheets have none, so their
  version number is used, and they're downloaded exported as .xlsx.

Both list subfolders too when the source asks for it, follow pagination,
and take an httpx client and a token provider so tests can stand in for the
real services.
"""
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlparse

import httpx

from app.config import get_settings
from app.sync.permissions import ANYONE, ORGANIZATION, Principal, principal

GRAPH = "https://graph.microsoft.com/v1.0"
DRIVE = "https://www.googleapis.com/drive/v3"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ConnectorError(Exception):
    """The repository can't be reached as configured (credentials, folder…)."""


@dataclass(frozen=True)
class RemoteFile:
    remote_id: str
    name: str            # file name, as it will be ingested
    path: str            # path inside the connected folder, for display
    version_tag: str     # changes when the content changes
    modified_at: datetime | None = None
    size: int | None = None
    # Google Sheets are exported as .xlsx instead of downloaded.
    export_mime_type: str | None = None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _check(response: httpx.Response, what: str) -> httpx.Response:
    if response.status_code >= 400:
        try:
            detail = response.json().get("error")
            detail = detail.get("message") if isinstance(detail, dict) else detail
        except ValueError:
            detail = response.text[:200]
        raise ConnectorError(f"{what} failed ({response.status_code}): {detail}")
    return response


# --- SharePoint (Microsoft Graph) ---------------------------------------------------------------

class GraphToken:
    """Client-credentials access token for Microsoft Graph, renewed before it expires."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str, client: httpx.Client):
        self.tenant_id, self.client_id, self.client_secret, self.client = tenant_id, client_id, client_secret, client
        self._token: str | None = None
        self._expires = 0.0

    def __call__(self) -> str:
        if self._token is None or time.monotonic() > self._expires:
            response = _check(self.client.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": self.client_id, "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
                },
            ), "SharePoint sign-in")
            body = response.json()
            self._token = body["access_token"]
            self._expires = time.monotonic() + int(body.get("expires_in", 3600)) - 60
        return self._token


class SharePointConnector:
    """A folder of a SharePoint document library (or OneDrive), either by
    path: {"site_url": "https://contoso.sharepoint.com/sites/Finance",
    "library": "Documents", "folder_path": "Corporate Finance/Q3 Reports"},
    or as picked in the Repositories tab: {"drive_id", "item_id"} (no
    item_id = the library's root). Both take "include_subfolders"."""

    def __init__(self, config: dict, token: Callable[[], str], client: httpx.Client):
        self.site_url = (config.get("site_url") or "").rstrip("/")
        self.library = config.get("library") or "Documents"
        self.folder_path = (config.get("folder_path") or "").strip("/")
        self.item_id = config.get("item_id")
        self.include_subfolders = bool(config.get("include_subfolders"))
        if not (self.site_url or config.get("drive_id")):
            raise ConnectorError("A SharePoint source needs site_url (e.g. https://contoso.sharepoint.com/sites/Finance).")
        self.token, self.client = token, client
        self._drive_id: str | None = config.get("drive_id")

    def _get(self, url: str, what: str, **params) -> httpx.Response:
        return _check(self.client.get(url, params=params or None, headers={"Authorization": f"Bearer {self.token()}"},
                                      follow_redirects=True), what)

    def drive_id(self) -> str:
        if self._drive_id is None:
            parsed = urlparse(self.site_url)
            site_path = parsed.path.strip("/")
            site_ref = f"{parsed.netloc}:/{site_path}" if site_path else parsed.netloc
            site = self._get(f"{GRAPH}/sites/{site_ref}", f"Finding the SharePoint site {self.site_url}").json()
            drives = self._get(f"{GRAPH}/sites/{site['id']}/drives", "Listing the site's document libraries").json()
            wanted = self.library.strip("/").lower()
            for drive in drives.get("value", []):
                library_path = urlparse(drive.get("webUrl", "")).path.rstrip("/").rsplit("/", 1)[-1].lower()
                if wanted in (drive.get("name", "").lower(), library_path.replace("%20", " ")):
                    self._drive_id = drive["id"]
                    break
            else:
                names = ", ".join(d.get("name", "?") for d in drives.get("value", []))
                raise ConnectorError(f"The site has no document library '{self.library}' (found: {names}).")
        return self._drive_id

    def _children(self, url: str) -> Iterator[dict]:
        params = {"$select": "id,name,file,folder,cTag,eTag,lastModifiedDateTime,size", "$top": "200"}
        while url:
            body = self._get(url, f"Listing SharePoint folder '{self.folder_path or '/'}'", **params).json()
            yield from body.get("value", [])
            url, params = body.get("@odata.nextLink"), {}  # the next link carries its own query

    def list_files(self) -> Iterator[RemoteFile]:
        drive = self.drive_id()
        root = f"{GRAPH}/drives/{drive}/root"
        if self.item_id:
            start = f"{GRAPH}/drives/{drive}/items/{self.item_id}/children"
        else:
            start = f"{root}:/{quote(self.folder_path)}:/children" if self.folder_path else f"{root}/children"
        pending = [(start, "")]
        while pending:
            url, prefix = pending.pop(0)
            for item in self._children(url):
                path = f"{prefix}{item['name']}"
                if "folder" in item:
                    if self.include_subfolders:
                        pending.append((f"{GRAPH}/drives/{drive}/items/{item['id']}/children", f"{path}/"))
                    continue
                if "file" not in item:
                    continue
                yield RemoteFile(
                    remote_id=item["id"], name=item["name"], path=path,
                    version_tag=item.get("cTag") or item.get("eTag") or item.get("lastModifiedDateTime", ""),
                    modified_at=_parse_time(item.get("lastModifiedDateTime")), size=item.get("size"),
                )

    def download(self, remote: RemoteFile) -> bytes:
        return self._get(f"{GRAPH}/drives/{self.drive_id()}/items/{remote.remote_id}/content",
                         f"Downloading {remote.path} from SharePoint").content

    def permissions(self, remote: RemoteFile) -> list[Principal]:
        """Who the file is shared with (ID-HU-INT-002), inherited permissions
        included: users, Microsoft 365 / security groups, SharePoint site
        groups ("Finance Members"), and sharing links ("anyone" for
        anonymous links, "organization" for organization-wide ones)."""
        url = f"{GRAPH}/drives/{self.drive_id()}/items/{remote.remote_id}/permissions"
        principals: list[Principal] = []
        while url:
            body = self._get(url, f"Reading the permissions of {remote.path}").json()
            for entry in body.get("value", []):
                identities = [entry.get("grantedToV2"), entry.get("grantedTo"),
                              *entry.get("grantedToIdentitiesV2", []), *entry.get("grantedToIdentities", [])]
                for identity in filter(None, identities):
                    for key in ("user", "group", "siteGroup", "siteUser"):
                        who = identity.get(key)
                        if who:
                            principals.append(principal(who.get("email"), who.get("displayName"),
                                                        who.get("loginName")))
                scope = (entry.get("link") or {}).get("scope")
                if scope == "anonymous":
                    principals.append(principal(ANYONE))
                elif scope == "organization":
                    principals.append(principal(ORGANIZATION))
            url = body.get("@odata.nextLink")
        return principals


# --- Google Drive -------------------------------------------------------------------------------

_FOLDER_URL = re.compile(r"/folders/([A-Za-z0-9_-]+)")


def folder_id_from(value: str) -> str:
    """A folder id, given the id or the folder's URL."""
    match = _FOLDER_URL.search(value or "")
    return match.group(1) if match else (value or "").strip()


class GoogleToken:
    """Access token of a service account (read-only Drive scope)."""

    def __init__(self, info: dict):
        from google.oauth2 import service_account

        self.credentials = service_account.Credentials.from_service_account_info(info, scopes=[DRIVE_SCOPE])

    def __call__(self) -> str:
        if not self.credentials.valid:
            from google.auth.transport.requests import Request

            try:
                self.credentials.refresh(Request())
            except Exception as exc:  # noqa: BLE001 — reported as a connection problem
                raise ConnectorError(f"Google Drive sign-in failed: {exc}") from exc
        return self.credentials.token


class GoogleDriveConnector:
    """A Google Drive folder shared with the service account: {"folder_id":
    "<id or folder URL>", "include_subfolders": false}."""

    def __init__(self, config: dict, token: Callable[[], str], client: httpx.Client):
        self.folder_id = folder_id_from(config.get("folder_id") or "")
        if not self.folder_id:
            raise ConnectorError("A Google Drive source needs folder_id (the folder's id or URL).")
        self.include_subfolders = bool(config.get("include_subfolders"))
        self.token, self.client = token, client

    def _get(self, url: str, what: str, **params) -> httpx.Response:
        return _check(self.client.get(url, params=params, headers={"Authorization": f"Bearer {self.token()}"},
                                      follow_redirects=True), what)

    def _children(self, folder_id: str) -> Iterator[dict]:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, md5Checksum, version, size)",
            "pageSize": "1000", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",  # shared drives too
        }
        while True:
            body = self._get(f"{DRIVE}/files", "Listing the Google Drive folder", **params).json()
            yield from body.get("files", [])
            if not body.get("nextPageToken"):
                return
            params["pageToken"] = body["nextPageToken"]

    def list_files(self) -> Iterator[RemoteFile]:
        pending = [(self.folder_id, "")]
        while pending:
            folder, prefix = pending.pop(0)
            for item in self._children(folder):
                mime = item.get("mimeType", "")
                path = f"{prefix}{item['name']}"
                if mime == GOOGLE_FOLDER:
                    if self.include_subfolders:
                        pending.append((item["id"], f"{path}/"))
                    continue
                if mime.startswith("application/vnd.google-apps.") and mime != GOOGLE_SHEET:
                    continue  # Docs, Slides, forms…: not financial workbooks or PDFs
                sheet = mime == GOOGLE_SHEET
                name = f"{item['name']}.xlsx" if sheet and not item["name"].lower().endswith(".xlsx") else item["name"]
                yield RemoteFile(
                    remote_id=item["id"], name=name, path=f"{prefix}{name}",
                    version_tag=item.get("md5Checksum") or f"v{item.get('version', '')}:{item.get('modifiedTime', '')}",
                    modified_at=_parse_time(item.get("modifiedTime")),
                    size=int(item["size"]) if item.get("size") else None,
                    export_mime_type=XLSX if sheet else None,
                )

    def download(self, remote: RemoteFile) -> bytes:
        if remote.export_mime_type:
            return self._get(f"{DRIVE}/files/{remote.remote_id}/export", f"Exporting {remote.path} from Google Drive",
                             mimeType=remote.export_mime_type).content
        return self._get(f"{DRIVE}/files/{remote.remote_id}", f"Downloading {remote.path} from Google Drive",
                         alt="media", supportsAllDrives="true").content

    def permissions(self, remote: RemoteFile) -> list[Principal]:
        """Who the file is shared with (ID-HU-INT-002): users and Google
        groups (email and name), a whole domain ("domain:<domain>") and
        anyone with the link ("anyone")."""
        params = {
            "fields": "nextPageToken, permissions(type, emailAddress, domain, displayName, deleted)",
            "supportsAllDrives": "true", "pageSize": "100",
        }
        principals: list[Principal] = []
        while True:
            body = self._get(f"{DRIVE}/files/{remote.remote_id}/permissions",
                             f"Reading the permissions of {remote.path}", **params).json()
            for entry in body.get("permissions", []):
                if entry.get("deleted"):
                    continue
                kind = entry.get("type")
                if kind == "anyone":
                    principals.append(principal(ANYONE))
                elif kind == "domain":
                    principals.append(principal(f"domain:{entry.get('domain', '')}"))
                else:
                    principals.append(principal(entry.get("emailAddress"), entry.get("displayName")))
            if not body.get("nextPageToken"):
                return principals
            params["pageToken"] = body["nextPageToken"]


# --- from a source's settings -------------------------------------------------------------------

def _google_info() -> dict:
    import json
    from pathlib import Path

    settings = get_settings()
    try:
        if settings.google_service_account_json:
            return json.loads(settings.google_service_account_json)
        if settings.google_service_account_file:
            return json.loads(Path(settings.google_service_account_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"The Google service account key can't be read: {exc}") from exc
    raise ConnectorError(
        "Google Drive isn't configured: set GOOGLE_SERVICE_ACCOUNT_FILE (or GOOGLE_SERVICE_ACCOUNT_JSON)."
    )


def build_connector(kind: str, config: dict, client: httpx.Client, token: Callable[[], str] | None = None):
    """The connector for a source: with `token` (a signed-in account's,
    app/sync/oauth.py) or else with service credentials from the environment."""
    if token is not None:
        if kind == "sharepoint":
            return SharePointConnector(config, token, client)
        if kind == "google_drive":
            return GoogleDriveConnector(config, token, client)
    settings = get_settings()
    if kind == "sharepoint":
        if not (settings.sharepoint_tenant_id and settings.sharepoint_client_id and settings.sharepoint_client_secret):
            raise ConnectorError(
                "SharePoint isn't configured: set SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID and "
                "SHAREPOINT_CLIENT_SECRET."
            )
        token = GraphToken(settings.sharepoint_tenant_id, settings.sharepoint_client_id,
                           settings.sharepoint_client_secret, client)
        return SharePointConnector(config, token, client)
    if kind == "google_drive":
        return GoogleDriveConnector(config, GoogleToken(_google_info()), client)
    raise ConnectorError(f"Unknown repository kind: {kind}")
