"""Retry policy for files that fail to sync (ID-HU-INT-003).

A failure never stops the rest of the folder (app/sync/service.py syncs file
by file). The failed file is retried with exponential backoff:

    attempt 1 fails -> retry after SYNC_RETRY_BACKOFF_MINUTES (15)
    attempt 2 fails -> after 2x (30), then 4x (60), 8x… capped at
                       SYNC_RETRY_MAX_BACKOFF_MINUTES (24 h)

Retries happen on the syncs that run once the time is due. After
SYNC_RETRY_MAX_ATTEMPTS failed attempts (5) the file is flagged
"needs_review": it isn't retried automatically any more, and the failure is
logged as an alert for administrators. It's attempted again when a reviewer
asks for it (POST /sync/files/{id}/retry), or by itself when the file
changes at the source (someone may have fixed it).

Every failed attempt is a SyncFailure in the failure log; when the file
finally syncs, its open entries are marked resolved.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import SyncedFile, SyncFailure, SyncSource
from app.sync.connectors import RemoteFile

logger = logging.getLogger(__name__)

FAILED = "failed"
NEEDS_REVIEW = "needs_review"


def backoff(attempts: int) -> timedelta:
    """How long to wait after the `attempts`-th failed attempt."""
    settings = get_settings()
    minutes = settings.sync_retry_backoff_minutes * 2 ** max(attempts - 1, 0)
    return timedelta(minutes=min(minutes, settings.sync_retry_max_backoff_minutes))


def is_due(record: SyncedFile, remote: RemoteFile, now: datetime) -> bool:
    """Whether to try a file that failed before; a file that changed at the
    source starts over (its attempts are reset)."""
    if record.status not in (FAILED, NEEDS_REVIEW):
        return True
    if record.version_tag != remote.version_tag:
        record.attempts, record.next_retry_at = 0, None
        return True
    if record.status == NEEDS_REVIEW:
        return False
    return record.next_retry_at is None or record.next_retry_at <= now


def record_failure(db: Session, source: SyncSource, record: SyncedFile, stage: str, error: str,
                   now: datetime, keep_status: bool = False) -> str:
    """Count a failed attempt of `record`, log it, and decide what's next:
    "failed" (a retry is scheduled) or "needs_review" (retries exhausted).
    With `keep_status` (a document that's still fine, e.g. only its
    permissions couldn't be read) the file's status isn't changed."""
    max_attempts = get_settings().sync_retry_max_attempts
    record.attempts = (record.attempts or 0) + 1
    record.error, record.synced_at = f"{stage}: {error}", now
    exhausted = record.attempts >= max_attempts
    record.next_retry_at = None if exhausted else now + backoff(record.attempts)
    if not keep_status:
        record.status = NEEDS_REVIEW if exhausted else FAILED
    db.add(SyncFailure(
        source_id=source.id, remote_id=record.remote_id, path=record.path, attempt=record.attempts, stage=stage,
        error=error[:2000], occurred_at=now, next_retry_at=record.next_retry_at, needs_review=exhausted,
    ))
    if exhausted:
        # The administrator alert: the failure log shows it flagged, and it's logged as an error.
        logger.error("Sync of %s from %s failed %s times (%s); flagged for manual review.",
                     record.path, source.name, record.attempts, error)
        return NEEDS_REVIEW
    logger.warning("Sync of %s from %s failed (attempt %s/%s, retry at %s): %s",
                   record.path, source.name, record.attempts, max_attempts, record.next_retry_at, error)
    return FAILED


def resolve(db: Session, record: SyncedFile, resolution: str, now: datetime) -> None:
    """The file synced (or left the folder): close its open failures."""
    if record.attempts or record.status in (FAILED, NEEDS_REVIEW):
        db.execute(
            update(SyncFailure)
            .where(SyncFailure.source_id == record.source_id, SyncFailure.remote_id == record.remote_id,
                   SyncFailure.resolved_at.is_(None))
            .values(resolved_at=now, resolution=resolution)
        )
    record.attempts, record.next_retry_at = 0, None


def retry_now(record: SyncedFile) -> None:
    """A reviewer asked to try the file again: a fresh set of attempts, due now."""
    record.attempts, record.next_retry_at = 0, None
    if record.status == NEEDS_REVIEW:
        record.status = FAILED
