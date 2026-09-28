"""Confidentiality policy for ingestion, retrieval and source access."""
from collections.abc import Iterable

PUBLIC = "public"
INTERNAL = "internal"
RESTRICTED = "restricted"
DEFAULT_TAG = RESTRICTED
ADMIN_ROLES = {"admin", "administrator"}

# Application roles map to permissions. A user may also send explicit permission
# names (for example ``compensation-access``) in X-User-Role for backwards
# compatibility with the existing prototype.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "analyst": {"public-access", "internal-access"},
    "reviewer": {"public-access", "internal-access"},
    "financial-controller": {
        "public-access",
        "internal-access",
        "compensation-access",
        "related-parties-access",
        "legal-access",
    },
    "controller": {
        "public-access",
        "internal-access",
        "compensation-access",
        "related-parties-access",
        "legal-access",
    },
    "restricted-reviewer": {"public-access", "internal-access", "restricted-access"},
    "admin": {"public-access", "internal-access", "restricted-access"},
    "administrator": {"public-access", "internal-access", "restricted-access"},
}

def normalize_tag(tag: str | None) -> str:
    value = (tag or "").strip().lower()
    if not value:
        return DEFAULT_TAG
    if value in {PUBLIC, INTERNAL, RESTRICTED}:
        return value
    if value.startswith("restricted:"):
        category = value.split(":", 1)[1].strip()
        if category:
            return f"restricted:{category}"
    raise ValueError("tag must be public, internal, restricted, or restricted:<category>")

def required_permission(tag: str) -> str | None:
    tag = normalize_tag(tag)
    if tag == PUBLIC:
        return "public-access"
    if tag == INTERNAL:
        return "internal-access"
    if tag == RESTRICTED:
        return "restricted-access"
    category = tag.split(":", 1)[1]
    aliases = {
        "compensation": "compensation-access",
        "related-parties": "related-parties-access",
        "related_parties": "related-parties-access",
        "legal": "legal-access",
        "legal-contingencies": "legal-access",
        "legal_contingencies": "legal-access",
    }
    return aliases.get(category, f"{category}-access")

def parse_roles(header_value: str | None) -> set[str]:
    return {x.strip().lower() for x in (header_value or "analyst").split(",") if x.strip()}

def permissions_for_roles(roles: Iterable[str]) -> set[str]:
    role_set = {r.strip().lower() for r in roles if r and r.strip()}
    permissions = set(role_set)
    for role in role_set:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    if role_set & ADMIN_ROLES:
        permissions.add("restricted-access")
    return permissions


def can_access(tag: str, roles: Iterable[str]) -> bool:
    tag = normalize_tag(tag)
    if tag == PUBLIC:
        return True
    permissions = permissions_for_roles(roles)
    if tag == INTERNAL:
        return "internal-access" in permissions
    permission = required_permission(tag)
    return permission in permissions or "restricted-access" in permissions
