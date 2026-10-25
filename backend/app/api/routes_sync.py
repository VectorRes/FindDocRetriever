"""ID-HU-INT-001: connected repository folders (SharePoint, Google Drive)
synced automatically every SYNC_INTERVAL_MINUTES. Reviewer roles only, like
approving versions: what's synced becomes queryable for everyone."""
import threading
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import RepositoryAccount, SyncedFile, SyncSource
from app.db.session import SessionLocal, get_db
from app.retrieval.access import normalize_tag, parse_roles
from app.schemas import SyncedFileOut, SyncOverviewOut, SyncRunOut, SyncSourceIn, SyncSourceOut
from app.source_tiers import TIERS
from app.sync.connectors import folder_id_from
from app.sync.scheduler import current_scheduler
from app.sync.service import is_running, sync_all
from app.versioning import APPROVER_ROLES

router = APIRouter()


def _require_reviewer(role: str) -> None:
    if not parse_roles(role) & APPROVER_ROLES:
        raise HTTPException(status_code=403, detail="Managing repository sync requires a reviewer role.")


def _source_out(db: Session, source: SyncSource) -> SyncSourceOut:
    files = db.scalar(select(func.count()).select_from(SyncedFile).where(SyncedFile.source_id == source.id))
    account = db.get(RepositoryAccount, source.account_id) if source.account_id else None
    return SyncSourceOut.model_validate(source).model_copy(update={
        "file_count": files or 0, "account_email": account.email if account else None,
    })


@router.get("/sync/sources", response_model=SyncOverviewOut)
def list_sources(
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> SyncOverviewOut:
    """The connected folders with their last sync, the interval, and whether
    a sync is running now."""
    _require_reviewer(x_user_role)
    scheduler = current_scheduler()
    sources = db.scalars(select(SyncSource).order_by(SyncSource.created_at)).all()
    return SyncOverviewOut(
        interval_minutes=get_settings().sync_interval_minutes,
        running=is_running(),
        next_run_at=scheduler.next_run_at if scheduler else None,
        sources=[_source_out(db, s) for s in sources],
    )


@router.post("/sync/sources", response_model=SyncSourceOut, status_code=201)
def create_source(
    request: SyncSourceIn,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> SyncSourceOut:
    """Connect a folder. SharePoint: config {"site_url", "library" (default
    "Documents"), "folder_path"}; Google Drive: config {"folder_id"} (the id
    or the folder's URL). Both accept "include_subfolders". With `account_id`
    (a signed-in account), the config is the one the folder browser gave
    ({"drive_id", "item_id"} or {"folder_id"}). It's synced on the next run
    (POST /sync/run to sync it now)."""
    _require_reviewer(x_user_role)
    config = dict(request.config)
    if request.account_id is not None:
        account = db.get(RepositoryAccount, request.account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        expected = {"google": "google_drive", "microsoft": "sharepoint"}[account.provider]
        if request.kind != expected:
            raise HTTPException(status_code=400, detail=f"A {account.provider} account connects {expected} folders.")
    if request.kind == "sharepoint":
        picked = request.account_id is not None and config.get("drive_id")  # from the folder browser
        if not picked and not config.get("site_url", "").startswith("https://"):
            raise HTTPException(status_code=400, detail="SharePoint needs config.site_url, e.g. https://contoso.sharepoint.com/sites/Finance.")
    else:
        config["folder_id"] = folder_id_from(config.get("folder_id", ""))
        if not config["folder_id"]:
            raise HTTPException(status_code=400, detail="Google Drive needs config.folder_id (the folder's id or URL).")
    if request.source_tier is not None and request.source_tier not in TIERS:
        raise HTTPException(status_code=400, detail=f"Unknown source tier. Use one of: {', '.join(TIERS)}.")
    try:
        tag = normalize_tag(request.confidentiality_tag)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source = SyncSource(
        kind=request.kind, name=request.name, config=config, confidentiality_tag=tag,
        source_tier=request.source_tier, auto_approve=request.auto_approve, enabled=request.enabled,
        last_summary={}, account_id=request.account_id,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_out(db, source)


@router.delete("/sync/sources/{source_id}", status_code=204)
def delete_source(
    source_id: UUID,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> None:
    """Disconnect a folder. Documents already ingested from it are kept."""
    _require_reviewer(x_user_role)
    source = db.get(SyncSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Sync source not found")
    db.delete(source)
    db.commit()


@router.get("/sync/sources/{source_id}/files", response_model=list[SyncedFileOut])
def list_synced_files(
    source_id: UUID,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> list[SyncedFileOut]:
    """Each file of the folder as last synced: the document it became, or why it failed."""
    _require_reviewer(x_user_role)
    if db.get(SyncSource, source_id) is None:
        raise HTTPException(status_code=404, detail="Sync source not found")
    return list(db.scalars(select(SyncedFile).where(SyncedFile.source_id == source_id).order_by(SyncedFile.path)))


@router.post("/sync/run", response_model=SyncRunOut, status_code=202)
def run_sync(
    source_id: UUID | None = None,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
) -> SyncRunOut:
    """Check the connected folders now, without waiting for the interval (all
    of them, or `source_id`). Runs in the background: GET /sync/sources shows
    when it's done and what it found. `started` is false if a sync is already running."""
    _require_reviewer(x_user_role)
    if is_running():
        return SyncRunOut(started=False)
    ids = [source_id] if source_id else None
    threading.Thread(target=sync_all, args=(SessionLocal,), kwargs={"source_ids": ids}, daemon=True).start()
    return SyncRunOut(started=True)
