"""Runs the repository sync every SYNC_INTERVAL_MINUTES (ID-HU-INT-001).

A daemon thread in the API process: a first check shortly after start-up,
then one every interval. Each check syncs every connected SharePoint and
Google Drive folder (app/sync/service.sync_all). A run that's still going
when the next one is due is not doubled up (the advisory lock).
"""
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(self, run: Callable[[], object], interval_seconds: float, first_delay_seconds: float = 10):
        self.run, self.interval, self.first_delay = run, interval_seconds, first_delay_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.next_run_at: datetime | None = None

    def start(self) -> None:
        if self._thread is None and self.interval > 0:
            self._thread = threading.Thread(target=self._loop, name="repository-sync", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        delay = self.first_delay
        while True:
            self.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            if self._stop.wait(delay):
                return
            try:
                self.run()
            except Exception:  # noqa: BLE001 — keep the schedule going
                logger.exception("Scheduled repository sync failed")
            delay = self.interval


_scheduler: SyncScheduler | None = None


def start_scheduler() -> SyncScheduler | None:
    """Start the app's scheduler if SYNC_INTERVAL_MINUTES > 0."""
    global _scheduler
    from app.config import get_settings
    from app.db.session import SessionLocal
    from app.sync.service import sync_all

    minutes = get_settings().sync_interval_minutes
    if minutes <= 0 or _scheduler is not None:
        return _scheduler
    _scheduler = SyncScheduler(lambda: sync_all(SessionLocal), minutes * 60)
    _scheduler.start()
    logger.info("Repository sync every %s minutes", minutes)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None


def current_scheduler() -> SyncScheduler | None:
    return _scheduler
