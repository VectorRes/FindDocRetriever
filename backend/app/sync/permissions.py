"""From a repository's sharing to FinDoc's access model (ID-HU-INT-002).

SharePoint and Google Drive share a file with users and groups; FinDoc
classifies each document with one confidentiality tag that roles may or may
not read (app/retrieval/access.py). A connected folder's access rules say
which tag each group stands for, e.g.:

    [{"principal": "Finance",         "tag": "internal"},
     {"principal": "Legal Corporate", "tag": "restricted:legal"},
     {"principal": "anyone",          "tag": "public"}]

A file's tag is decided by the rules its sharing matches:
  - the least restrictive matched tag wins: a file shared with Finance and
    with Legal is readable by Finance, so it's "internal";
  - two different restricted categories (legal and compensation) can't be
    expressed as one tag, so the file is "restricted" (fail-closed);
  - no rule matches (e.g. only its owner can see it): "restricted".

Principals are matched case-insensitively against any name the repository
gives them (email, display name, SharePoint group name), or the special
"anyone" (anonymous link), "organization" (organization-wide link) and
"domain:<domain>" (Google Drive domain sharing).
"""
from dataclasses import dataclass

from app.retrieval.access import INSIDER, INTERNAL, PUBLIC, RESTRICTED, normalize_tag

ANYONE = "anyone"
ORGANIZATION = "organization"


@dataclass(frozen=True)
class Principal:
    """Someone a file is shared with, by every name the repository gives."""

    names: tuple[str, ...]

    @property
    def label(self) -> str:
        return self.names[0] if self.names else "?"


def principal(*names: str | None) -> Principal:
    return Principal(tuple(dict.fromkeys(n.strip() for n in names if n and n.strip())))


def _rank(tag: str) -> int:
    if tag == PUBLIC:
        return 0
    if tag == INTERNAL:
        return 1
    if tag == INSIDER:
        return 4
    if tag == RESTRICTED:
        return 3
    return 2  # restricted:<category>


def validate_rules(rules: list[dict]) -> list[dict]:
    """Rules with their tags normalized; ValueError for a bad one."""
    clean = []
    for rule in rules:
        name = str(rule.get("principal", "")).strip()
        if not name:
            raise ValueError("Each access rule needs a principal (a group, user, 'anyone' or 'organization').")
        clean.append({"principal": name, "tag": normalize_tag(rule.get("tag"))})
    return clean


def resolve_access(principals: list[Principal], rules: list[dict]) -> tuple[str, list[str]]:
    """The tag for a file shared with `principals`, and the rules' principals
    that matched (see module docstring)."""
    names = {name.lower() for p in principals for name in p.names}
    matched = [rule for rule in rules if rule["principal"].strip().lower() in names]
    if not matched:
        return RESTRICTED, []
    tags = {normalize_tag(rule["tag"]) for rule in matched}
    best = min(_rank(tag) for tag in tags)
    candidates = {tag for tag in tags if _rank(tag) == best}
    tag = candidates.pop() if len(candidates) == 1 else RESTRICTED
    return tag, [rule["principal"] for rule in matched]
