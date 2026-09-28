"""Version control for documents (ID-HU-BE-015).

Every document belongs to a *version group*: the family of uploads that are
versions of the same logical document (e.g. "Budget_v1.xlsx" and
"Budget_v2.xlsx"). Within a group, exactly one ready document is the
*default version* (`is_current=True`) — the one QA retrieval uses:

  - the most recent **approved** version, or
  - if nothing in the group has been approved yet, the most recent version
    (so a freshly uploaded, single-version document is still usable).

A newer draft uploaded next to an approved version does NOT replace it: it
waits as "pending approval" until approved. Older versions stay linked to
their successor via `superseded_by_id` and remain viewable for audit.
"""
import re
from datetime import datetime, timezone
from pathlib import PurePath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus

DRAFT = "draft"
APPROVED = "approved"

# Roles allowed to approve a version. Like the rest of access control, the
# role is taken from X-User-Role at face value until real auth exists.
APPROVER_ROLES = {
    "reviewer",
    "restricted-reviewer",
    "financial-controller",
    "controller",
    "admin",
    "administrator",
}

# A trailing version marker: "_v2", " v2.1", "-ver3", "_version 4", or a
# browser-style copy suffix " (1)". The separator before "v" is required so
# names like "Sales_nov2" aren't mistaken for "Sales_no" + "v2".
_VERSION_SUFFIX = re.compile(
    r"(?:(?:^|[\s_.\-]+)(?:v|ver|version)[\s_.\-]?\d+(?:\.\d+)*|\s*\(\d+\))$",
    re.IGNORECASE,
)
_SEPARATORS = re.compile(r"[\s_.\-]+")


def derive_version_group(filename: str, doc_type: str = "excel") -> str:
    """Group key shared by every version of the same logical document.

    "Budget_v1.xlsx", "Budget_v2.xlsx" and "budget.xlsx" all map to
    "excel:budget"; "Budget_2025_v1.xlsx" maps to "excel:budget 2025" (a
    different period is a different document, not a new version). The
    doc_type is part of the key so a PDF and a workbook never collide.
    """
    stem = PurePath(filename).stem.strip().lower()
    previous = None
    while previous != stem:
        previous = stem
        stem = _VERSION_SUFFIX.sub("", stem).strip()
    normalized = _SEPARATORS.sub(" ", stem).strip() or PurePath(filename).stem.lower()
    return f"{doc_type}:{normalized}"


def assign_version(db: Session, document: Document, version_of: Document | None = None) -> None:
    """Place `document` in its version group as the newest version.

    The group is derived from the filename unless `version_of` explicitly
    names an existing document to version (for files whose names don't
    follow the "_vN" convention)."""
    document.version_group = (
        version_of.version_group
        if version_of is not None
        else derive_version_group(document.filename, document.doc_type)
    )
    highest = db.scalar(
        select(func.max(Document.version_number)).where(
            Document.version_group == document.version_group,
            Document.id != document.id,
        )
    )
    document.version_number = (highest or 0) + 1


def recompute_group(db: Session, version_group: str) -> Document | None:
    """Re-derive which version of the group is the default and relink the
    supersession chain. Call after any change to a group's membership or
    approvals (upload, approve, delete). Doesn't commit."""
    db.flush()
    members = db.scalars(
        select(Document)
        .where(Document.version_group == version_group)
        .order_by(Document.version_number)
    ).all()
    ready = [m for m in members if m.status == DocumentStatus.ready.value]
    approved = [m for m in ready if m.approval_status == APPROVED]
    candidates = approved or ready
    default = max(candidates, key=lambda m: m.version_number) if candidates else None

    for member in members:
        member.is_current = default is not None and member.id == default.id
        member.superseded_by_id = None

    if default is not None:
        # Each older ready version links to the next one up to the default,
        # e.g. v1 -> v2 -> v3(default). Newer drafts (pending approval) stay
        # unlinked: they haven't replaced anything yet.
        chain = [m for m in ready if m.version_number < default.version_number] + [default]
        for older, newer in zip(chain, chain[1:]):
            older.superseded_by_id = newer.id

    db.flush()
    return default


def approve(db: Session, document: Document) -> Document | None:
    """Mark `document` approved; it becomes the default if it's the newest
    approved version, superseding the previous one. Doesn't commit."""
    document.approval_status = APPROVED
    document.approved_at = datetime.now(timezone.utc)
    return recompute_group(db, document.version_group)


def current_version_of(db: Session, document: Document) -> Document | None:
    """The default version of `document`'s group (may be `document` itself)."""
    return db.scalar(
        select(Document).where(
            Document.version_group == document.version_group,
            Document.is_current.is_(True),
        )
    )


def versions_of(db: Session, document: Document) -> list[Document]:
    """Every version in `document`'s group, newest first."""
    return list(
        db.scalars(
            select(Document)
            .where(Document.version_group == document.version_group)
            .order_by(Document.version_number.desc())
        )
    )
