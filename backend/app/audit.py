"""Access audit trail (ID-HU-BE-013 / ID-HU-BE-014).

Every access attempt is a `RetrievalAuditLog` row with the user, roles,
document, confidentiality tier and timestamp:

- **Granted** attempts (content handed to the user or the model) are added to
  the caller's session, so they're committed with the rest of the request.
- **Denied** attempts are written in their own session and committed right
  away, so a denial stays on record even if the request fails afterwards
  (e.g. the model service is down and the request is rolled back). If writing
  it fails, the error is logged and access stays denied.

Each API request gets a `request_id` and a `resource` (chat, ratios, file…)
from `AuditContextMiddleware`; a request's repeated denials of the same
content (e.g. the bilingual search runs retrieval twice) are logged once.
"""
import logging
import threading
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.retrieval.access import denial_reason, is_insider

logger = logging.getLogger(__name__)


class _RequestScope:
    def __init__(self, resource: str):
        self.request_id = uuid.uuid4().hex
        self.resource = resource
        self.denied: set[tuple] = set()
        self.lock = threading.Lock()


_scope: ContextVar[_RequestScope | None] = ContextVar("audit_scope", default=None)

# Path prefix -> resource name, most specific first.
_RESOURCES = (
    ("/query/answer", "chat"),
    ("/query", "query"),
    ("/ratios", "ratios"),
    ("/compare", "compare"),
    ("/dashboard", "dashboard"),
    ("/audit", "audit"),
)


def resource_for_path(path: str) -> str:
    for prefix, name in _RESOURCES:
        if path.startswith(prefix):
            return name
    if path.startswith("/documents/"):
        if path.endswith("/resolve"):
            return "resolve"
        if path.endswith("/file"):
            return "file"
        if "/cells/" in path:
            return "cells"
        return "documents"
    return "api"


@contextmanager
def audit_scope(resource: str):
    """One request's audit context (what the middleware sets up per request)."""
    token = _scope.set(_RequestScope(resource))
    try:
        yield _scope.get()
    finally:
        _scope.reset(token)


class AuditContextMiddleware:
    """Gives each HTTP request its audit `request_id` and `resource`."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with audit_scope(resource_for_path(scope.get("path", ""))):
            await self.app(scope, receive, send)


def _current() -> tuple[str | None, str]:
    scope = _scope.get()
    return (scope.request_id, scope.resource) if scope else (None, "internal")


def _roles(roles: Iterable[str]) -> list[str]:
    return sorted({role for role in roles if role})


def record_granted(
    db: Session, *, user_id: str | None, roles: Iterable[str], document_id, chunk_id,
    document_filename: str | None, tag: str, question: str, action: str = "retrieve",
) -> None:
    """Content the user was allowed to see and received (in the caller's session)."""
    from app.db.models import RetrievalAuditLog

    request_id, resource = _current()
    db.add(RetrievalAuditLog(
        user_id=user_id or "anonymous", roles=_roles(roles), action=action,
        document_id=document_id, chunk_id=chunk_id, document_filename=document_filename,
        confidentiality_tag=tag, authorized=True, is_insider=is_insider(tag),
        question=question or "", resource=resource, request_id=request_id,
    ))


@dataclass(frozen=True)
class Denial:
    """Content or a document the user's roles didn't allow."""

    document_id: object
    document_filename: str | None
    tag: str
    chunk_id: object = None
    action: str = "retrieve"
    reason: str | None = None


def record_denials(
    *, user_id: str | None, roles: Iterable[str], denials: Iterable[Denial], question: str | None,
) -> None:
    """Log denied attempts, committed on their own (see module docstring)."""
    from app.db.models import RetrievalAuditLog
    from app.db.session import SessionLocal

    scope = _scope.get()
    request_id, resource = _current()
    pending = []
    for denial in denials:
        key = (str(denial.document_id), str(denial.chunk_id), denial.action)
        if scope is not None:
            with scope.lock:
                if key in scope.denied:
                    continue
                scope.denied.add(key)
        pending.append(denial)
    if not pending:
        return

    session = SessionLocal()
    try:
        for denial in pending:
            session.add(RetrievalAuditLog(
                user_id=user_id or "anonymous", roles=_roles(roles), action=denial.action,
                document_id=denial.document_id, chunk_id=denial.chunk_id,
                document_filename=denial.document_filename, confidentiality_tag=denial.tag,
                authorized=False, is_insider=is_insider(denial.tag), question=question or "",
                reason=denial.reason or denial_reason(denial.tag), resource=resource, request_id=request_id,
            ))
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Could not write %d access-denial audit event(s)", len(pending))
    finally:
        session.close()
