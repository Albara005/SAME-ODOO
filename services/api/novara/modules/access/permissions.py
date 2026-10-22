"""Permission catalog and the default roles every organization starts with.

Permissions are checked on the server for every sensitive operation; hiding
a button in the UI is never treated as protection.
"""

from __future__ import annotations

ORG_MANAGE = "org.manage"
MEMBERS_READ = "members.read"
MEMBERS_MANAGE = "members.manage"
ROLES_MANAGE = "roles.manage"
AUDIT_READ = "audit.read"
FILES_READ = "files.read"
FILES_WRITE = "files.write"
FILES_DELETE = "files.delete"

CATALOG: dict[str, str] = {
    ORG_MANAGE: "Edit organization settings",
    MEMBERS_READ: "View members",
    MEMBERS_MANAGE: "Add, suspend and remove members, change their roles",
    ROLES_MANAGE: "Create and edit roles",
    AUDIT_READ: "Read the audit log",
    FILES_READ: "View and download files",
    FILES_WRITE: "Upload files",
    FILES_DELETE: "Delete files",
}

OWNER_ROLE = "owner"

SYSTEM_ROLES: dict[str, tuple[str, list[str]]] = {
    OWNER_ROLE: ("Owner", sorted(CATALOG)),
    "admin": ("Administrator", sorted(set(CATALOG) - {ORG_MANAGE})),
    "member": ("Member", [MEMBERS_READ, FILES_READ, FILES_WRITE]),
    "viewer": ("Viewer", [FILES_READ]),
}


def unknown_permissions(perms: list[str]) -> list[str]:
    return [p for p in perms if p not in CATALOG]
