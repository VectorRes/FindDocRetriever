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
from app.db.models import RepositoryAccount, SyncedFile, SyncFailure, SyncSource
from app.db.session import SessionLocal, get_db
from app.retrieval.access import normalize_tag, parse_roles
from app.schemas import (
    SyncedFileOut, SyncFailureOut, SyncOverviewOut, SyncRunOut, SyncSourceIn, SyncSourceOut, SyncSourceUpdate,
)
from app.sync import retry
from app.source_tiers import TIERS
from app.sync.connectors import folder_id_from
from app.sync.permissions import validate_rules
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
        needs_review=db.scalar(select(func.count()).select_from(SyncedFile)
                               .where(SyncedFile.status == retry.NEEDS_REVIEW)) or 0,
        open_failures=db.scalar(select(func.count()).select_from(SyncFailure)
                                .where(SyncFailure.resolved_at.is_(None))) or 0,
        retry_max_attempts=get_settings().sync_retry_max_attempts,
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
        rules = validate_rules(request.permission_rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source = SyncSource(
        kind=request.kind, name=request.name, config=config, confidentiality_tag=tag,
        source_tier=request.source_tier, auto_approve=request.auto_approve, enabled=request.enabled,
        last_summary={}, account_id=request.account_id, permission_rules=rules,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_out(db, source)


@router.patch("/sync/sources/{source_id}", response_model=SyncSourceOut)
def update_source(
    source_id: UUID,
    request: SyncSourceUpdate,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> SyncSourceOut:
    """Change a connected folder's settings, e.g. its access rules
    (ID-HU-INT-002). New rules apply to every file on the next sync."""
    _require_reviewer(x_user_role)
    source = db.get(SyncSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Sync source not found")
    try:
        if request.confidentiality_tag is not None:
            source.confidentiality_tag = normalize_tag(request.confidentiality_tag)
        if request.permission_rules is not None:
            source.permission_rules = validate_rules(request.permission_rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for field in ("name", "auto_approve", "enabled"):
        value = getattr(request, field)
        if value is not None:
            setattr(source, field, value)
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


@router.get("/sync/failures", response_model=list[SyncFailureOut])
def list_failures(
    source_id: UUID | None = None,
    unresolved: bool = False,
    limit: int = 200,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> list[SyncFailureOut]:
    """ID-HU-INT-003: the sync failure log, newest first: every failed
    attempt with its step and cause, when it will be retried, whether it was
    flagged for manual review (retries exhausted) and how it was resolved."""
    _require_reviewer(x_user_role)
    query = select(SyncFailure, SyncSource.name).join(SyncSource, SyncSource.id == SyncFailure.source_id)
    if source_id:
        query = query.where(SyncFailure.source_id == source_id)
    if unresolved:
        query = query.where(SyncFailure.resolved_at.is_(None))
    rows = db.execute(query.order_by(SyncFailure.occurred_at.desc()).limit(min(max(limit, 1), 1000))).all()
    return [SyncFailureOut.model_validate(f).model_copy(update={"source_name": name}) for f, name in rows]


@router.post("/sync/files/{file_id}/retry", response_model=SyncRunOut, status_code=202)
def retry_file(
    file_id: UUID,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    db: Session = Depends(get_db),
) -> SyncRunOut:
    """ID-HU-INT-003: try a failed file again now — e.g. one flagged for
    manual review once its cause was fixed — with a fresh set of attempts."""
    _require_reviewer(x_user_role)
    record = db.get(SyncedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Synced file not found")
    if record.status not in (retry.FAILED, retry.NEEDS_REVIEW):
        raise HTTPException(status_code=400, detail="Only a file that failed to sync can be retried.")
    retry.retry_now(record)
    db.commit()
    if is_running():
        return SyncRunOut(started=False)
    threading.Thread(target=sync_all, args=(SessionLocal,), kwargs={"source_ids": [record.source_id]},
                     daemon=True).start()
    return SyncRunOut(started=True)


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
