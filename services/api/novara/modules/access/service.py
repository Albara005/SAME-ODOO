from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novara.modules.access.models import Role
from novara.modules.access.permissions import OWNER_ROLE, SYSTEM_ROLES
from novara.modules.organizations.models import Membership


def create_system_roles(db: Session) -> dict[str, Role]:
    roles = {key: Role(key=key, name=name, permissions=perms, is_system=True) for key, (name, perms) in SYSTEM_ROLES.items()}
    db.add_all(roles.values())
    db.flush()
    return roles


def active_owner_count(db: Session) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Membership)
        .join(Role, Role.id == Membership.role_id)
        .where(Role.key == OWNER_ROLE, Membership.status == "active")
    )
