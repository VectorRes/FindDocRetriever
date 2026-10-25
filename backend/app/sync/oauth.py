"""Signing in with Google or Microsoft to connect repository folders
(ID-HU-INT-001), instead of service credentials.

1. `authorization_url` sends the reviewer to the provider's consent page
   (authorization-code flow, offline access so there's a refresh token),
   with a `state` signed with TOKEN_ENCRYPTION_KEY that names the provider
   and the user and expires in 10 minutes (CSRF protection).
2. `complete_sign_in` checks the state, exchanges the code for tokens,
   reads who signed in, and saves the account with its tokens encrypted
   (Fernet). Signing in again with the same account updates it.
3. `account_token` gives the connectors an access token, renewed from the
   refresh token when it expires; Microsoft rotates refresh tokens, so the
   new one is saved.

Scopes are read-only: Google `drive.readonly`; Microsoft `Files.Read.All`
and `Sites.Read.All` (OneDrive and SharePoint), plus profile and offline
access. Some organizations require an administrator's consent for them.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import RepositoryAccount

STATE_TTL_SECONDS = 600

PROVIDERS = {
    "google": {
        "kind": "google_drive",
        "label": "Google Drive",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": "openid email profile https://www.googleapis.com/auth/drive.readonly",
        # A refresh token every time, even when the account consented before.
        "extra": {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
    },
    "microsoft": {
        "kind": "sharepoint",
        "label": "SharePoint / OneDrive",
        "authorize_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "scopes": "openid email profile offline_access User.Read Files.Read.All Sites.Read.All",
        "extra": {"prompt": "select_account"},
    },
}


class OAuthError(Exception):
    """Signing in or renewing access failed; the message says why."""


def http_client() -> httpx.Client:
    """The HTTP client for the providers (tests replace it)."""
    return httpx.Client(timeout=30)


def _credentials(provider: str) -> tuple[str, str]:
    settings = get_settings()
    client_id, secret = {
        "google": (settings.google_oauth_client_id, settings.google_oauth_client_secret),
        "microsoft": (settings.microsoft_oauth_client_id, settings.microsoft_oauth_client_secret),
    }[provider]
    if not (client_id and secret):
        variable = provider.upper()
        raise OAuthError(
            f"Sign-in with {PROVIDERS[provider]['label']} isn't set up: set {variable}_OAUTH_CLIENT_ID and "
            f"{variable}_OAUTH_CLIENT_SECRET (see README)."
        )
    return client_id, secret


def configured_providers() -> dict[str, bool]:
    configured = {}
    for provider in PROVIDERS:
        try:
            _credentials(provider)
            configured[provider] = bool(get_settings().token_encryption_key)
        except OAuthError:
            configured[provider] = False
    return configured


def _url(template: str) -> str:
    return template.format(tenant=get_settings().microsoft_oauth_tenant)


def redirect_uri(provider: str) -> str:
    return f"{get_settings().oauth_redirect_base_url.rstrip('/')}/oauth/{provider}/callback"


# --- encryption and the signed state ------------------------------------------------------------

def _key() -> bytes:
    key = get_settings().token_encryption_key
    if not key:
        raise OAuthError("TOKEN_ENCRYPTION_KEY isn't set, so account tokens can't be stored securely (see README).")
    return key.encode()


def encrypt(value: str) -> str:
    from cryptography.fernet import Fernet

    return Fernet(_key()).encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    from cryptography.fernet import Fernet, InvalidToken

    try:
        return Fernet(_key()).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise OAuthError("The stored account token can't be decrypted (was TOKEN_ENCRYPTION_KEY changed?).") from exc


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(provider: str, user_id: str, now: float | None = None) -> str:
    payload = _b64(json.dumps({
        "p": provider, "u": user_id, "n": secrets.token_urlsafe(8),
        "exp": int((now or time.time()) + STATE_TTL_SECONDS),
    }).encode())
    signature = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_state(state: str, provider: str, now: float | None = None) -> str:
    """The user the state was issued to; OAuthError if it's forged, expired or for another provider."""
    try:
        payload, signature = state.split(".", 1)
        expected = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise OAuthError("The sign-in response is malformed; start again.") from exc
    if not hmac.compare_digest(signature, expected):
        raise OAuthError("The sign-in response wasn't issued by this app; start again.")
    if data.get("p") != provider:
        raise OAuthError("The sign-in response is for another provider; start again.")
    if (now or time.time()) > data.get("exp", 0):
        raise OAuthError("The sign-in took too long; start again.")
    return data.get("u") or "anonymous"


# --- the flow -----------------------------------------------------------------------------------

def authorization_url(provider: str, user_id: str) -> str:
    if provider not in PROVIDERS:
        raise OAuthError(f"Unknown provider: {provider}")
    client_id, _ = _credentials(provider)
    config = PROVIDERS[provider]
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri(provider), "response_type": "code",
        "scope": config["scopes"], "state": sign_state(provider, user_id), **config["extra"],
    }
    return f"{_url(config['authorize_url'])}?{urlencode(params)}"


def _token_request(provider: str, client: httpx.Client, **data) -> dict:
    client_id, secret = _credentials(provider)
    response = client.post(_url(PROVIDERS[provider]["token_url"]), data={
        "client_id": client_id, "client_secret": secret, **data,
    })
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code >= 400 or "access_token" not in body:
        reason = body.get("error_description") or body.get("error") or response.text[:200]
        raise OAuthError(f"{PROVIDERS[provider]['label']} refused the sign-in: {reason}")
    return body


def _profile(provider: str, access_token: str, client: httpx.Client) -> tuple[str, str | None]:
    headers = {"Authorization": f"Bearer {access_token}"}
    if provider == "google":
        response = client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
        body = response.json() if response.status_code < 400 else {}
        email, name = body.get("email"), body.get("name")
    else:
        response = client.get("https://graph.microsoft.com/v1.0/me", headers=headers,
                              params={"$select": "displayName,mail,userPrincipalName"})
        body = response.json() if response.status_code < 400 else {}
        email, name = body.get("mail") or body.get("userPrincipalName"), body.get("displayName")
    if not email:
        raise OAuthError(f"Couldn't read which {PROVIDERS[provider]['label']} account signed in.")
    return email, name


def _expiry(body: dict) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in", 3600)) - 60)


def complete_sign_in(db: Session, provider: str, code: str, state: str) -> RepositoryAccount:
    if provider not in PROVIDERS:
        raise OAuthError(f"Unknown provider: {provider}")
    user_id = verify_state(state, provider)
    with http_client() as client:
        tokens = _token_request(provider, client, grant_type="authorization_code", code=code,
                                redirect_uri=redirect_uri(provider))
        if not tokens.get("refresh_token"):
            raise OAuthError(
                f"{PROVIDERS[provider]['label']} didn't grant offline access, so folders couldn't be synced later."
            )
        email, name = _profile(provider, tokens["access_token"], client)
    account = db.scalar(select(RepositoryAccount).where(
        RepositoryAccount.provider == provider, RepositoryAccount.email == email,
    )) or RepositoryAccount(provider=provider, email=email)
    account.display_name = name
    account.refresh_token = encrypt(tokens["refresh_token"])
    account.access_token = encrypt(tokens["access_token"])
    account.access_token_expires_at = _expiry(tokens)
    account.scopes = tokens.get("scope", PROVIDERS[provider]["scopes"])
    account.connected_by = user_id
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def account_token(db: Session, account: RepositoryAccount, client: httpx.Client) -> Callable[[], str]:
    """An access-token provider for the connectors, renewing it when needed."""

    def token() -> str:
        expires = account.access_token_expires_at
        if account.access_token and expires and expires > datetime.now(timezone.utc):
            return decrypt(account.access_token)
        body = _token_request(account.provider, client, grant_type="refresh_token",
                              refresh_token=decrypt(account.refresh_token))
        account.access_token = encrypt(body["access_token"])
        account.access_token_expires_at = _expiry(body)
        if body.get("refresh_token"):  # Microsoft rotates it
            account.refresh_token = encrypt(body["refresh_token"])
        db.commit()
        return body["access_token"]

    return token
