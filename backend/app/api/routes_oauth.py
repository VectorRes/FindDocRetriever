"""ID-HU-INT-001: sign in with Google or Microsoft (OAuth) and browse the
account's folders, to connect them for automatic sync without service
credentials. Reviewer roles only, like the rest of repository sync.

The browser flow: the Repositories tab asks GET /oauth/{provider}/start for
the provider's sign-in URL and goes there; the provider sends the browser
back to GET /oauth/{provider}/callback, which saves the account and returns
to the tab (?oauth=connected, or ?oauth_error=<why>)."""
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes_sync import _require_reviewer
from app.config import get_settings
from app.db.models import RepositoryAccount
from app.db.session import get_db
from app.schemas import FolderNodeOut, OAuthProviderOut, OAuthStartOut, RepositoryAccountOut
from app.sync import oauth
from app.sync.browse import browse
from app.sync.connectors import ConnectorError

router = APIRouter()


@router.get("/oauth/providers", response_model=list[OAuthProviderOut])
def list_providers() -> list[OAuthProviderOut]:
    """Which sign-ins are set up (their OAuth app credentials are configured)."""
    configured = oauth.configured_providers()
    return [
        OAuthProviderOut(provider=p, label=c["label"], kind=c["kind"], configured=configured[p],
                         redirect_uri=oauth.redirect_uri(p))
        for p, c in oauth.PROVIDERS.items()
    ]


@router.get("/oauth/{provider}/start", response_model=OAuthStartOut)
def start_sign_in(
    provider: str,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
) -> OAuthStartOut:
    """The provider's sign-in page to send the browser to."""
    _require_reviewer(x_user_role)
    if provider not in oauth.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    try:
        return OAuthStartOut(authorize_url=oauth.authorization_url(provider, x_user_id))
    except oauth.OAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _back_to_app(**params) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().frontend_url.rstrip('/')}/?{urlencode(params)}#repositories",
                            status_code=303)


@router.get("/oauth/{provider}/callback")
def sign_in_callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Where the provider sends the browser after the sign-in."""
    if error or not code or not state:
        return _back_to_app(oauth_error=error_description or error or "The sign-in was cancelled.")
    try:
        account = oauth.complete_sign_in(db, provider, code, state)
    except oauth.OAuthError as exc:
        return _back_to_app(oauth_error=str(exc))
    return _back_to_app(oauth="connected", provider=provider, account=account.email)


@router.get("/repository-accounts", response_model=list[RepositoryAccountOut])
def list_accounts(
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> list[RepositoryAccountOut]:
    _require_reviewer(x_user_role)
    return list(db.scalars(select(RepositoryAccount).order_by(RepositoryAccount.created_at)))


@router.delete("/repository-accounts/{account_id}", status_code=204)
def disconnect_account(
    account_id: UUID,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> None:
    """Forget the account and its tokens; the folders connected through it
    stop syncing. Documents already ingested are kept."""
    _require_reviewer(x_user_role)
    account = db.get(RepositoryAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    db.delete(account)
    db.commit()


@router.get("/repository-accounts/{account_id}/folders", response_model=list[FolderNodeOut])
def browse_folders(
    account_id: UUID,
    parent: str | None = None,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> list[FolderNodeOut]:
    """The folders inside `parent` (the top level without it). Folders that
    can be connected carry the `config` to send to POST /sync/sources."""
    _require_reviewer(x_user_role)
    account = db.get(RepositoryAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        with oauth.http_client() as client:
            nodes = browse(account.provider, client, oauth.account_token(db, account, client), parent)
    except (oauth.OAuthError, ConnectorError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [FolderNodeOut.model_validate(node) for node in nodes]
