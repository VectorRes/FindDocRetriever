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

Every ingested document gets the source's classification (so analysts can
query it right away), its tier if set (ID-HU-BE-016, else inferred from the
name) and, with auto_approve, is approved so an update replaces the previous
version immediately. Files deleted from the repository aren't removed here.

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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus, RepositoryAccount, SyncedFile, SyncSource
from app.ingestion.service import ALLOWED_EXTENSIONS, ingest_path
from app.observability import span, trace
from app.sync.connectors import ConnectorError, RemoteFile, build_connector
from app.sync.oauth import OAuthError, account_token
from app.versioning import approve

logger = logging.getLogger(__name__)

# Postgres advisory lock held while a sync runs (any 64-bit number unique to this use).
SYNC_LOCK_KEY = 4_601_200_017

ConnectorFactory = Callable[[str, dict, httpx.Client], object]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ingest(db: Session, source: SyncSource, connector, remote: RemoteFile, previous: Document | None) -> Document:
    suffix = Path(remote.name).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(connector.download(remote))
        tmp_path = Path(tmp.name)
    try:
        document = ingest_path(db, remote.name, tmp_path, version_of=previous)
    finally:
        tmp_path.unlink(missing_ok=True)
    if document.status != DocumentStatus.ready.value:
        raise RuntimeError(document.error_message or "ingestion failed")
    document.confidentiality_tag = source.confidentiality_tag
    for chunk in document.chunks:
        chunk.confidentiality_tag = source.confidentiality_tag
    if source.source_tier:
        document.source_tier = source.source_tier
    if source.auto_approve:
        approve(db, document)
    db.commit()
    return document


def sync_source(db: Session, source: SyncSource, connector_factory: ConnectorFactory = build_connector,
                client: httpx.Client | None = None) -> dict:
    """Sync one source (see module docstring); returns the counts of the run."""
    summary = {"new": 0, "updated": 0, "unchanged": 0, "failed": 0, "skipped": 0}
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
        errors = []
        for remote in remotes:
            if Path(remote.name).suffix.lower() not in ALLOWED_EXTENSIONS:
                summary["skipped"] += 1
                continue
            record = known.get(remote.remote_id)
            previous = db.get(Document, record.document_id) if record and record.document_id else None
            if record and record.status == "ingested" and record.version_tag == remote.version_tag and previous:
                summary["unchanged"] += 1
                continue
            outcome = "updated" if previous is not None else "new"
            try:
                document = _ingest(db, source, connector, remote, previous)
                status, error, document_id = "ingested", None, document.id
            except Exception as exc:  # noqa: BLE001 — one bad file mustn't stop the folder
                db.rollback()
                logger.warning("Sync of %s failed", remote.path, exc_info=True)
                outcome, status, error, document_id = "failed", "failed", str(exc), (previous.id if previous else None)
                errors.append(f"{remote.path}: {exc}")
            if record is None:
                record = SyncedFile(source_id=source.id, remote_id=remote.remote_id)
                db.add(record)
                known[remote.remote_id] = record
            record.path, record.version_tag, record.remote_modified_at = remote.path, remote.version_tag, remote.modified_at
            record.document_id, record.status, record.error, record.synced_at = document_id, status, error, _now()
            summary[outcome] += 1
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
