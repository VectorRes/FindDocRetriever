"""Incremental sync of connected repository folders (ID-HU-INT-001).

For each enabled source, the connector lists the folder's files with a
version tag; each one is compared with what was last synced:

  - a file never seen          -> ingested ("new");
  - a file whose tag changed   -> ingested as a new version of the document it
                                  became last time ("updated", ID-HU-BE-015);
  - a file with the same tag   -> left alone ("unchanged"), no download;
  - not a .xlsx/.xlsm/.pdf     -> ignored ("skipped");
  - a download or ingestion that fails is recorded ("failed") and retried on
    the next sync.

Every ingested document gets its classification (so analysts can query it
right away), its tier if set (ID-HU-BE-016, else inferred from the name)
and, with auto_approve, is approved so an update replaces the previous
version immediately.

ID-HU-INT-002, the repository's access is propagated:
  - with access rules on the source, each file's permissions are read on
    every sync and turned into its classification (app/sync/permissions.py);
    when that changes — tightened or loosened — the document and all its
    versions are re-classified ("permissions_changed"). Without rules, the
    source's fixed classification is used and existing documents are left
    as reviewers classified them;
  - a file no longer in the folder is "removed": every version of its
    document stops answering queries (kept for audit). If it comes back
    unchanged it's "restored"; edited, it's ingested as a new version;
  - a file whose permissions can't be read is left as it was ("failed").

ID-HU-INT-003, failures: each file is synced on its own, so one that fails
(download, ingestion of a corrupted file, permissions) never blocks the
rest. It's logged in the failure log and retried with backoff; after
SYNC_RETRY_MAX_ATTEMPTS it's flagged "needs_review" with an alert for
administrators (app/sync/retry.py). A corrupted version that failed to
ingest is discarded, so retries don't pile up failed documents.

`sync_all` checks every enabled source of both repositories in one run, under
a Postgres advisory lock so two runs (the scheduler and POST /sync/run, or
two API processes) never sync at the same time.
"""
import logging
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, DocumentStatus, RepositoryAccount, SyncedFile, SyncSource
from app.ingestion.service import ALLOWED_EXTENSIONS, ingest_path
from app.observability import span, trace
from app.sync.connectors import ConnectorError, RemoteFile, build_connector
from app.sync.oauth import OAuthError, account_token
from app.storage import delete_document_file
from app.sync import retry
from app.sync.permissions import resolve_access
from app.versioning import approve, recompute_group, versions_of

logger = logging.getLogger(__name__)

# Postgres advisory lock held while a sync runs (any 64-bit number unique to this use).
SYNC_LOCK_KEY = 4_601_200_017

ConnectorFactory = Callable[[str, dict, httpx.Client], object]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _versions(db: Session, document: Document) -> list[Document]:
    return versions_of(db, document)


def _set_access(db: Session, document: Document, tag: str) -> None:
    """`tag` on the document, every version of it and all their chunks, so a
    superseded version scoped to explicitly is restricted too."""
    versions = _versions(db, document)
    for version in versions:
        version.confidentiality_tag = tag
    db.execute(update(Chunk).where(Chunk.document_id.in_([v.id for v in versions]))
               .values(confidentiality_tag=tag))


def _remove(db: Session, document: Document) -> None:
    """Deleted from the repository: every version stops answering queries
    (kept for audit; app/retrieval/vector_store.py never searches it)."""
    for version in _versions(db, document):
        if version.status == DocumentStatus.ready.value:
            version.status = DocumentStatus.removed.value
    recompute_group(db, document.version_group)


def _restore(db: Session, document: Document) -> None:
    for version in _versions(db, document):
        if version.status == DocumentStatus.removed.value:
            version.status = DocumentStatus.ready.value
    recompute_group(db, document.version_group)


def _access(source: SyncSource, connector, remote: RemoteFile) -> tuple[str, list[str]]:
    """The file's tag and who the repository shares it with: from its
    permissions when the source has access rules (ID-HU-INT-002), else the
    source's fixed classification."""
    if not source.permission_rules:
        return source.confidentiality_tag, []
    principals = connector.permissions(remote)
    tag, _ = resolve_access(principals, source.permission_rules)
    return tag, [p.label for p in principals]


class SyncStepError(Exception):
    """A file couldn't be synced: at which step ("download", "ingestion",
    "permissions") and why — what the failure log records (ID-HU-INT-003)."""

    def __init__(self, stage: str, error: str):
        super().__init__(f"{stage}: {error}")
        self.stage, self.error = stage, error


def _discard(db: Session, document: Document) -> None:
    """A version whose ingestion failed: removed, so retries don't pile up
    failed copies in the document list."""
    group = document.version_group
    delete_document_file(document.storage_path)
    db.delete(document)
    recompute_group(db, group)
    db.commit()


def _ingest(db: Session, source: SyncSource, connector, remote: RemoteFile, previous: Document | None,
            tag: str) -> Document:
    suffix = Path(remote.name).suffix.lower()
    try:
        content = connector.download(remote)
    except Exception as exc:  # noqa: BLE001 — network, permissions, a file the repository can't serve
        raise SyncStepError("download", str(exc)) from exc
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        document = ingest_path(db, remote.name, tmp_path, version_of=previous)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise SyncStepError("ingestion", str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    if document.status != DocumentStatus.ready.value:
        reason = document.error_message or "ingestion failed"
        _discard(db, document)  # a corrupted file
        raise SyncStepError("ingestion", reason)
    if previous is not None and previous.status == DocumentStatus.removed.value:
        _restore(db, previous)  # an edited file back in the folder: its history is too
    _set_access(db, document, tag)
    if source.source_tier:
        document.source_tier = source.source_tier
    if source.auto_approve:
        approve(db, document)
    db.commit()
    return document


def _record(db: Session, source: SyncSource, known: dict, remote: RemoteFile) -> SyncedFile:
    """The file's record, created on first sight, with what the folder says now."""
    record = known.get(remote.remote_id)
    if record is None:
        record = SyncedFile(source_id=source.id, remote_id=remote.remote_id, status=retry.FAILED, attempts=0)
        db.add(record)
        known[remote.remote_id] = record
    record.path, record.version_tag, record.remote_modified_at = remote.path, remote.version_tag, remote.modified_at
    return record


def sync_source(db: Session, source: SyncSource, connector_factory: ConnectorFactory = build_connector,
                client: httpx.Client | None = None) -> dict:
    """Sync one source (see module docstring); returns the counts of the run."""
    summary = {"new": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": 0,
               "permissions_changed": 0, "removed": 0, "restored": 0,
               "retry_pending": 0, "needs_review": 0}
    own_client = client is None
    client = client or httpx.Client(timeout=120)
    with span(f"sync {source.kind}: {source.name}", input={"config": source.config}) as traced:
        try:
            if source.account_id:
                # Connected by signing in: synced with that account's access.
                account = db.get(RepositoryAccount, source.account_id)
                token = account_token(db, account, client)
                connector = connector_factory(source.kind, source.config, client, token=token)
            else:
                connector = connector_factory(source.kind, source.config, client)
            remotes = list(connector.list_files())
        except (ConnectorError, OAuthError, httpx.HTTPError) as exc:
            source.last_sync_at, source.last_status, source.last_error = _now(), "error", str(exc)
            db.commit()
            traced.update(output={"error": str(exc)})
            if own_client:
                client.close()
            return summary

        known = {f.remote_id: f for f in db.scalars(select(SyncedFile).where(SyncedFile.source_id == source.id))}
        propagate = bool(source.permission_rules)
        errors, seen = [], set()
        for remote in remotes:
            if Path(remote.name).suffix.lower() not in ALLOWED_EXTENSIONS:
                summary["skipped"] += 1
                continue
            seen.add(remote.remote_id)
            now = _now()
            record = known.get(remote.remote_id)
            # ID-HU-INT-003: a file that failed waits for its retry; one whose
            # retries ran out waits for a reviewer (or for the file to change).
            if record is not None and not retry.is_due(record, remote, now):
                summary["needs_review" if record.status == retry.NEEDS_REVIEW else "retry_pending"] += 1
                continue
            previous = db.get(Document, record.document_id) if record and record.document_id else None
            unchanged_content = record is not None and record.version_tag == remote.version_tag and previous
            try:
                tag, shared_with = _access(source, connector, remote)
            except (ConnectorError, OAuthError, httpx.HTTPError) as exc:
                # Who may see it is unknown: change nothing about its access.
                record = _record(db, source, known, remote)
                still_fine = bool(unchanged_content) and record.status == "ingested"
                outcome = retry.record_failure(db, source, record, "permissions", str(exc), now, keep_status=still_fine)
                summary[outcome] += 1
                errors.append(f"{remote.path}: {exc}")
                db.commit()
                continue
            if unchanged_content and record.status == "ingested":
                if propagate and previous.confidentiality_tag != tag:
                    _set_access(db, previous, tag)  # tightened or loosened in the repository
                    summary["permissions_changed"] += 1
                else:
                    summary["unchanged"] += 1
                record.permissions, record.access_tag = shared_with, (tag if propagate else record.access_tag)
                retry.resolve(db, record, "permissions read", now)
                db.commit()
                continue
            if unchanged_content and record.status == "removed":
                _restore(db, previous)  # back in the folder (e.g. restored from the recycle bin)
                _set_access(db, previous, tag if propagate else previous.confidentiality_tag)
                outcome, document_id = "restored", previous.id
            else:
                outcome = "updated" if previous is not None else "new"
                try:
                    document = _ingest(db, source, connector, remote, previous, tag)
                    document_id = document.id
                except SyncStepError as exc:
                    # One bad file never stops the rest of the folder.
                    record = _record(db, source, known, remote)
                    summary[retry.record_failure(db, source, record, exc.stage, exc.error, now)] += 1
                    errors.append(f"{remote.path}: {exc}")
                    db.commit()
                    continue
            record = _record(db, source, known, remote)
            retried = bool(record.attempts)
            retry.resolve(db, record, "retry succeeded" if retried else "synced", now)
            record.document_id, record.status, record.error, record.synced_at = document_id, "ingested", None, now
            record.permissions, record.access_tag = shared_with, tag
            summary[outcome] += 1
            db.commit()

        # Files no longer in the folder: their documents stop answering queries.
        for remote_id, record in known.items():
            if remote_id in seen or record.status == "removed":
                continue
            document = db.get(Document, record.document_id) if record.document_id else None
            if document is not None:
                _remove(db, document)
            retry.resolve(db, record, "left the folder", _now())
            record.status, record.error, record.synced_at = "removed", None, _now()
            summary["removed"] += 1
            db.commit()

        source.last_sync_at, source.last_summary = _now(), summary
        source.last_status = "error" if errors else "ok"
        source.last_error = "; ".join(errors)[:2000] or None
        db.commit()
        traced.update(output=summary)
    if own_client:
        client.close()
    return summary


_running = threading.Lock()


def is_running() -> bool:
    return _running.locked()


def sync_all(session_factory: Callable[[], Session], connector_factory: ConnectorFactory = build_connector,
             source_ids: list | None = None) -> dict | None:
    """Sync every enabled source (or only `source_ids`), SharePoint and
    Google Drive alike. None when a sync is already running."""
    if not _running.acquire(blocking=False):
        return None
    try:
        db = session_factory()
        lock = db.get_bind().connect()  # the advisory lock lives on this one connection
        try:
            if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": SYNC_LOCK_KEY}):
                return None
            try:
                query = select(SyncSource).where(SyncSource.enabled.is_(True)).order_by(SyncSource.created_at)
                if source_ids:
                    query = query.where(SyncSource.id.in_(source_ids))
                results = {}
                with trace("sync", input={"sources": [str(s) for s in source_ids or []] or "all"}, tags=["sync"]) as traced:
                    with httpx.Client(timeout=120) as client:
                        for source in db.scalars(query).all():
                            results[str(source.id)] = sync_source(db, source, connector_factory, client)
                    traced.update(output=results)
                return results
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SYNC_LOCK_KEY})
                lock.commit()
        finally:
            lock.close()
            db.close()
    finally:
        _running.release()
