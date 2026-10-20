"""ID-HU-BE-014: the access audit trail, for administrators.

Every access attempt — granted or denied — is in `retrieval_audit_logs`
(see app/audit.py). Administrators can filter it by user, document, time
period, outcome, confidentiality tier and origin, and export it as CSV to
answer an audit request. Anyone else gets 403, and that attempt is logged too.
"""
import csv
import io
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import Denial, record_denials
from app.db.models import RetrievalAuditLog
from app.db.session import get_db
from app.retrieval.access import ADMIN_ROLES, parse_roles
from app.schemas import AuditLogOut, AuditLogPage

router = APIRouter()

MAX_PAGE = 500
MAX_EXPORT_ROWS = 50_000


class AuditFilters:
    """Query-string filters shared by the list and the CSV export."""

    def __init__(
        self,
        user_id: Annotated[str | None, Query(description="Exact user id")] = None,
        document_id: Annotated[UUID | None, Query()] = None,
        document: Annotated[str | None, Query(description="Part of the document's filename")] = None,
        date_from: Annotated[datetime | None, Query(alias="from")] = None,
        date_to: Annotated[datetime | None, Query(alias="to")] = None,
        authorized: Annotated[bool | None, Query(description="true: granted only, false: denied only")] = None,
        tier: Annotated[str | None, Query(description="Confidentiality tag, e.g. restricted:compensation")] = None,
        insider_only: bool = False,
        resource: Annotated[str | None, Query(description="chat, ratios, compare, file…")] = None,
    ):
        self.user_id = user_id
        self.document_id = document_id
        self.document = document
        self.date_from = date_from
        self.date_to = date_to
        self.authorized = authorized
        self.tier = tier
        self.insider_only = insider_only
        self.resource = resource

    def apply(self, stmt):
        log = RetrievalAuditLog
        if self.user_id:
            stmt = stmt.where(log.user_id == self.user_id.strip())
        if self.document_id:
            stmt = stmt.where(log.document_id == self.document_id)
        if self.document:
            stmt = stmt.where(log.document_filename.ilike(f"%{_escape_like(self.document.strip())}%", escape="\\"))
        if self.date_from:
            stmt = stmt.where(log.occurred_at >= self.date_from)
        if self.date_to:
            stmt = stmt.where(log.occurred_at <= self.date_to)
        if self.authorized is not None:
            stmt = stmt.where(log.authorized.is_(self.authorized))
        if self.tier:
            stmt = stmt.where(log.confidentiality_tag == self.tier.strip().lower())
        if self.insider_only:
            stmt = stmt.where(log.is_insider.is_(True))
        if self.resource:
            stmt = stmt.where(log.resource == self.resource.strip().lower())
        return stmt


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _require_admin(role: str, user_id: str, what: str) -> None:
    roles = parse_roles(role)
    if roles & ADMIN_ROLES:
        return
    record_denials(
        user_id=user_id, roles=roles, question=what,
        denials=[Denial(document_id=None, document_filename=None, tag="admin-only", action="audit_query",
                        reason="The audit trail is only available to administrators.")],
    )
    raise HTTPException(status_code=403, detail="The audit trail is only available to administrators.")


def _ordered(filters: AuditFilters):
    return filters.apply(select(RetrievalAuditLog)).order_by(
        RetrievalAuditLog.occurred_at.desc(), RetrievalAuditLog.id
    )


@router.get("/audit/access-logs", response_model=AuditLogPage)
def list_access_logs(
    filters: Annotated[AuditFilters, Depends()],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
    db: Session = Depends(get_db),
) -> AuditLogPage:
    """Access attempts, newest first, granted and denied."""
    _require_admin(x_user_role, x_user_id, "Query the access audit trail")
    total = db.scalar(filters.apply(select(func.count()).select_from(RetrievalAuditLog)))
    items = db.scalars(_ordered(filters).limit(limit).offset(offset)).all()
    return AuditLogPage(total=total or 0, limit=limit, offset=offset, items=list(items))


CSV_COLUMNS = (
    "occurred_at", "user_id", "roles", "outcome", "document_filename", "document_id", "chunk_id",
    "confidentiality_tag", "is_insider", "action", "resource", "reason", "question", "request_id",
)


def _cell(value) -> str:
    text = "" if value is None else str(value)
    # Keep spreadsheet apps from evaluating a logged question as a formula.
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


@router.get("/audit/access-logs/export.csv")
def export_access_logs(
    filters: Annotated[AuditFilters, Depends()],
    x_user_role: Annotated[str, Header(alias="X-User-Role")] = "analyst",
    x_user_id: Annotated[str, Header(alias="X-User-Id")] = "anonymous",
    db: Session = Depends(get_db),
) -> Response:
    """The filtered audit trail as CSV (newest first)."""
    _require_admin(x_user_role, x_user_id, "Export the access audit trail")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for log in db.scalars(_ordered(filters).limit(MAX_EXPORT_ROWS)):
        writer.writerow([_cell(v) for v in (
            log.occurred_at.isoformat(), log.user_id, ", ".join(log.roles or []),
            "granted" if log.authorized else "denied", log.document_filename, log.document_id, log.chunk_id,
            log.confidentiality_tag, log.is_insider, log.action, log.resource, log.reason, log.question,
            log.request_id,
        )])
    return Response(
        content=out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="access-audit.csv"'},
    )
