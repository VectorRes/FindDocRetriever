"""ID-HU-INT-001 — signing in with Google / Microsoft to connect folders.

The providers (token endpoints, profiles, Drive and Graph listings) are
played by an httpx.MockTransport; no real accounts. Accounts are stored in
the test database with their tokens encrypted.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api.routes_oauth as routes_oauth
from app.config import Settings
from app.db.models import RepositoryAccount, SyncSource
from app.db.session import SessionLocal
from app.sync import oauth
from app.sync.browse import browse
from app.sync.connectors import RemoteFile
from app.sync.service import sync_all

KEY = Fernet.generate_key().decode()


@pytest.fixture
def settings(monkeypatch):
    configured = Settings(
        google_oauth_client_id="google-client", google_oauth_client_secret="google-secret",
        microsoft_oauth_client_id="ms-client", microsoft_oauth_client_secret="ms-secret",
        microsoft_oauth_tenant="organizations", oauth_redirect_base_url="http://localhost:8000",
        frontend_url="http://localhost:5173", token_encryption_key=KEY,
    )
    monkeypatch.setattr(oauth, "get_settings", lambda: configured)
    monkeypatch.setattr(routes_oauth, "get_settings", lambda: configured)
    return configured


class Provider:
    """Plays the providers' OAuth and API endpoints; records the requests."""

    def __init__(self, email):
        self.email = email
        self.requests: list[httpx.Request] = []
        self.refresh_token = "refresh-1"
        self.grant_refresh = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        form = parse_qs(request.content.decode()) if request.method == "POST" else {}
        if url in ("https://oauth2.googleapis.com/token",
                   "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"):
            if form.get("grant_type") == ["authorization_code"]:
                if form.get("code") != ["good-code"]:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code."})
                body = {"access_token": "access-1", "expires_in": 3600, "scope": "drive.readonly"}
                if self.grant_refresh:
                    body["refresh_token"] = self.refresh_token
                return httpx.Response(200, json=body)
            if form.get("grant_type") == ["refresh_token"]:
                assert form["refresh_token"] == [self.refresh_token]
                self.refresh_token = "refresh-2"  # rotated, as Microsoft does
                return httpx.Response(200, json={"access_token": "access-2", "expires_in": 3600,
                                                 "refresh_token": "refresh-2"})
        if url.startswith("https://openidconnect.googleapis.com/v1/userinfo"):
            return httpx.Response(200, json={"email": self.email, "name": "Ana Analista"})
        if url.startswith("https://graph.microsoft.com/v1.0/me?"):
            return httpx.Response(200, json={"userPrincipalName": self.email, "displayName": "Ana Analista"})
        # Folder browsing
        if url.startswith("https://www.googleapis.com/drive/v3/drives"):
            return httpx.Response(200, json={"drives": [{"id": "sd1", "name": "Finance Team"}]})
        if url.startswith("https://www.googleapis.com/drive/v3/files"):
            q = request.url.params["q"]
            if q.startswith("'root' in parents"):
                return httpx.Response(200, json={"files": [{"id": "fin", "name": "Finance"}]})
            if q.startswith("sharedWithMe"):
                return httpx.Response(200, json={"files": [{"id": "mr", "name": "Management Reports"}]})
        if url.startswith("https://graph.microsoft.com/v1.0/sites?"):
            return httpx.Response(200, json={"value": [{"id": "s1", "displayName": "Finance"}]})
        if url == "https://graph.microsoft.com/v1.0/sites/s1/drives":
            return httpx.Response(200, json={"value": [{"id": "b!d1", "name": "Documents"}]})
        if url.startswith("https://graph.microsoft.com/v1.0/drives/b!d1/root/children"):
            return httpx.Response(200, json={"value": [
                {"id": "cf", "name": "Corporate Finance", "folder": {}},
                {"id": "x", "name": "readme.docx", "file": {}},
            ]})
        return httpx.Response(404, json={"error": {"message": f"no route {url}"}})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def provider(monkeypatch, db_session):
    fake = Provider(f"ana-{uuid.uuid4().hex[:6]}@example.com")
    monkeypatch.setattr(oauth, "http_client", fake.client)
    yield fake
    db_session.rollback()
    for account in db_session.scalars(select(RepositoryAccount).where(RepositoryAccount.email == fake.email)):
        db_session.delete(account)
    db_session.commit()


# --- the signed state ------------------------------------------------------------------------------

def test_state_round_trip_names_the_user(settings):
    assert oauth.verify_state(oauth.sign_state("google", "ana"), "google") == "ana"


@pytest.mark.parametrize("tamper", ["forged", "provider", "expired"])
def test_a_bad_state_is_refused(settings, tamper):
    state = oauth.sign_state("google", "ana")
    with pytest.raises(oauth.OAuthError):
        if tamper == "forged":
            payload, signature = state.split(".")
            oauth.verify_state(f"{payload}x.{signature}", "google")
        elif tamper == "provider":
            oauth.verify_state(state, "microsoft")
        else:
            oauth.verify_state(state, "google", now=time.time() + oauth.STATE_TTL_SECONDS + 1)


def test_tokens_are_encrypted_at_rest(settings):
    secret = oauth.encrypt("refresh-1")
    assert secret != "refresh-1" and oauth.decrypt(secret) == "refresh-1"


# --- the sign-in -----------------------------------------------------------------------------------

def test_sign_in_urls(settings):
    google = urlparse(oauth.authorization_url("google", "ana"))
    params = {k: v[0] for k, v in parse_qs(google.query).items()}
    assert google.netloc == "accounts.google.com"
    assert params["client_id"] == "google-client" and params["access_type"] == "offline"
    assert params["redirect_uri"] == "http://localhost:8000/oauth/google/callback"
    assert "drive.readonly" in params["scope"]
    microsoft = urlparse(oauth.authorization_url("microsoft", "ana"))
    params = {k: v[0] for k, v in parse_qs(microsoft.query).items()}
    assert microsoft.path == "/organizations/oauth2/v2.0/authorize"
    assert {"offline_access", "Files.Read.All", "Sites.Read.All"} <= set(params["scope"].split())


def test_sign_in_isnt_offered_without_the_oauth_app(monkeypatch):
    monkeypatch.setattr(oauth, "get_settings", lambda: Settings(token_encryption_key=KEY))
    assert oauth.configured_providers() == {"google": False, "microsoft": False}
    with pytest.raises(oauth.OAuthError, match="GOOGLE_OAUTH_CLIENT_ID"):
        oauth.authorization_url("google", "ana")


@pytest.mark.parametrize("name", ["google", "microsoft"])
def test_completing_the_sign_in_saves_the_account(settings, provider, db_session, name):
    account = oauth.complete_sign_in(db_session, name, "good-code", oauth.sign_state(name, "ana"))

    assert (account.provider, account.email, account.connected_by) == (name, provider.email, "ana")
    assert account.refresh_token != "refresh-1" and oauth.decrypt(account.refresh_token) == "refresh-1"
    # Signing in again with the same account updates it.
    again = oauth.complete_sign_in(db_session, name, "good-code", oauth.sign_state(name, "ana"))
    assert again.id == account.id


def test_no_offline_access_is_refused(settings, provider, db_session):
    provider.grant_refresh = False
    with pytest.raises(oauth.OAuthError, match="offline access"):
        oauth.complete_sign_in(db_session, "google", "good-code", oauth.sign_state("google", "ana"))


def test_access_token_is_renewed_and_rotated_refresh_token_saved(settings, provider, db_session):
    account = oauth.complete_sign_in(db_session, "microsoft", "good-code", oauth.sign_state("microsoft", "ana"))
    with provider.client() as client:
        token = oauth.account_token(db_session, account, client)
        assert token() == "access-1"  # still valid: no request
        assert not any(r.method == "POST" for r in provider.requests[3:])
        account.access_token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert token() == "access-2"
    assert oauth.decrypt(account.refresh_token) == "refresh-2"


# --- browsing ---------------------------------------------------------------------------------------

def test_browse_google(provider):
    with provider.client() as client:
        top = browse("google", client, lambda: "t", None)
        assert [(n.id, n.name, n.selectable) for n in top] == [
            ("root", "My Drive", True), ("shared", "Shared with me", False), ("sd1", "Finance Team", True),
        ]
        [shared] = browse("google", client, lambda: "t", "shared")
        assert (shared.name, shared.config) == ("Management Reports", {"folder_id": "mr"})


def test_browse_microsoft_sites_libraries_and_folders(provider):
    with provider.client() as client:
        assert [n.id for n in browse("microsoft", client, lambda: "t", None)] == ["me", "sites"]
        [site] = browse("microsoft", client, lambda: "t", "sites")
        [library] = browse("microsoft", client, lambda: "t", site.id)
        assert (library.name, library.config) == ("Documents", {"drive_id": "b!d1"})
        [folder] = browse("microsoft", client, lambda: "t", library.id)  # the .docx isn't a folder
        assert (folder.name, folder.config) == ("Corporate Finance", {"drive_id": "b!d1", "item_id": "cf"})


# --- syncing with the account ---------------------------------------------------------------------

def test_a_folder_connected_by_signing_in_syncs_with_that_account(settings, provider, db_session):
    account = oauth.complete_sign_in(db_session, "microsoft", "good-code", oauth.sign_state("microsoft", "ana"))
    source = SyncSource(kind="sharepoint", name="Corporate Finance", config={"drive_id": "b!d1", "item_id": "cf"},
                        confidentiality_tag="internal", last_summary={}, account_id=account.id)
    db_session.add(source)
    db_session.commit()
    seen = {}

    class Repo:
        def list_files(self):
            seen["token"] = seen["get_token"]()
            return iter([RemoteFile(remote_id="r", name="notes.docx", path="notes.docx", version_tag="t")])

    def factory(kind, config, client, token=None):
        seen.update(kind=kind, config=config, get_token=token)
        return Repo()

    try:
        sync_all(SessionLocal, connector_factory=factory, source_ids=[source.id])
        assert seen["kind"] == "sharepoint" and seen["config"]["item_id"] == "cf"
        assert seen["token"] == "access-1"  # the account's own access
    finally:
        db_session.delete(db_session.get(SyncSource, source.id))
        db_session.commit()


# --- API --------------------------------------------------------------------------------------------

@pytest.fixture
def client():
    from app.main import app

    return TestClient(app, follow_redirects=False)


REVIEWER = {"X-User-Role": "reviewer", "X-User-Id": "ana"}


def test_providers_and_start(client, settings):
    providers = {p["provider"]: p for p in client.get("/oauth/providers").json()}
    assert providers["google"]["configured"] and providers["microsoft"]["kind"] == "sharepoint"
    assert providers["google"]["redirect_uri"] == "http://localhost:8000/oauth/google/callback"
    assert client.get("/oauth/google/start").status_code == 403
    url = client.get("/oauth/google/start", headers=REVIEWER).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    assert oauth.verify_state(state, "google") == "ana"


def test_callback_saves_the_account_and_returns_to_the_tab(client, settings, provider, db_session):
    state = oauth.sign_state("google", "ana")
    response = client.get("/oauth/google/callback", params={"code": "good-code", "state": state})

    assert response.status_code == 303
    target = urlparse(response.headers["location"])
    assert (target.netloc, target.fragment) == ("localhost:5173", "repositories")
    assert parse_qs(target.query)["oauth"] == ["connected"]
    assert db_session.scalar(select(RepositoryAccount).where(RepositoryAccount.email == provider.email))


@pytest.mark.parametrize("params", [
    {"error": "access_denied", "error_description": "The user declined."},
    {"code": "bad-code", "state": "x.y"},
])
def test_callback_failures_return_to_the_tab_with_the_reason(client, settings, provider, params):
    response = client.get("/oauth/google/callback", params=params)
    assert response.status_code == 303
    assert parse_qs(urlparse(response.headers["location"]).query)["oauth_error"]


def test_accounts_browse_connect_and_disconnect(client, settings, provider, db_session):
    account = oauth.complete_sign_in(db_session, "microsoft", "good-code", oauth.sign_state("microsoft", "ana"))

    listed = client.get("/repository-accounts", headers=REVIEWER).json()
    assert any(a["id"] == str(account.id) and a["email"] == provider.email for a in listed)
    folders = client.get(f"/repository-accounts/{account.id}/folders", params={"parent": "drive|b!d1"},
                         headers=REVIEWER).json()
    assert folders == [{"id": "item|b!d1|cf", "name": "Corporate Finance", "selectable": True,
                        "config": {"drive_id": "b!d1", "item_id": "cf"}, "browsable": True}]

    wrong_kind = client.post("/sync/sources", headers=REVIEWER, json={
        "kind": "google_drive", "name": "x", "config": {"folder_id": "f"}, "account_id": str(account.id),
    })
    assert wrong_kind.status_code == 400
    created = client.post("/sync/sources", headers=REVIEWER, json={
        "kind": "sharepoint", "name": "Corporate Finance", "config": folders[0]["config"],
        "account_id": str(account.id), "auto_approve": True,
    })
    assert created.status_code == 201
    assert created.json()["account_email"] == provider.email

    assert client.delete(f"/repository-accounts/{account.id}", headers=REVIEWER).status_code == 204
    db_session.expire_all()
    assert db_session.get(SyncSource, uuid.UUID(created.json()["id"])) is None  # its folders are disconnected
