"""Automatic organization (tenant) isolation for every ORM query.

Any model inheriting ``TenantScoped`` belongs to exactly one organization.
Once a session is bound to an organization with ``bind_tenant``:

* every SELECT / UPDATE / DELETE touching a tenant-scoped model is filtered to
  that organization, without the developer writing the condition by hand;
* new rows get ``organization_id`` filled from the context, and a row that
  names a different organization is rejected at flush time;
* moving an existing row to another organization is rejected.

Querying a tenant-scoped model on a session with *no* tenant bound raises
``TenantContextMissing``, so a forgotten context fails loudly instead of
leaking every organization's rows. Platform-level code that legitimately
spans organizations (e.g. "list the organizations I belong to") must opt in
explicitly with ``system_scope``, which keeps such places easy to audit.

On PostgreSQL the same organization id is also pushed into the
``app.current_org`` setting at the start of each transaction, so the
row-level security policies in ``migrations/postgres`` act as a second,
independent layer of defence.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, event, inspect, text
from sqlalchemy.orm import Mapped, ORMExecuteState, Session, declared_attr, mapped_column, with_loader_criteria

_TENANT_KEY = "novara_tenant_org_id"
_USER_KEY = "novara_tenant_user_id"
_BYPASS_KEY = "novara_tenant_bypass"
# session.info key -> PostgreSQL setting read by the row-level security policies.
_PG_SETTINGS = {_TENANT_KEY: "app.current_org", _USER_KEY: "app.current_user"}


class TenantContextMissing(RuntimeError):
    """A tenant-scoped model was accessed without an organization bound."""


class TenantViolation(RuntimeError):
    """A write tried to cross an organization boundary."""


class TenantScoped:
    """Mixin for rows owned by a single organization."""

    @declared_attr
    def organization_id(cls) -> Mapped[str]:
        return mapped_column(
            String(36), ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
        )


def bind_tenant(session: Session, organization_id: str) -> None:
    current = session.info.get(_TENANT_KEY)
    if current is not None and current != organization_id:
        raise TenantViolation("session is already bound to a different organization")
    session.info[_TENANT_KEY] = organization_id
    if session.in_transaction():
        _push_pg_settings(session, session.connection())


def bind_user(session: Session, user_id: str) -> None:
    """Record the authenticated user; PostgreSQL policies use it to let users see their own memberships."""
    session.info[_USER_KEY] = user_id
    if session.in_transaction():
        _push_pg_settings(session, session.connection())


def current_tenant(session: Session) -> str | None:
    return session.info.get(_TENANT_KEY)


@contextmanager
def system_scope(session: Session) -> Iterator[Session]:
    """Temporarily lift tenant filtering for explicit cross-organization access."""
    previous = session.info.get(_BYPASS_KEY, False)
    session.info[_BYPASS_KEY] = True
    try:
        yield session
    finally:
        session.info[_BYPASS_KEY] = previous


def _bypassed(session: Session) -> bool:
    return bool(session.info.get(_BYPASS_KEY))


def _touches_tenant_model(state: ORMExecuteState) -> bool:
    return any(issubclass(m.class_, TenantScoped) for m in state.all_mappers)


@event.listens_for(Session, "do_orm_execute")
def _filter_by_tenant(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load or state.is_relationship_load:
        # Lazy/refresh loads inherit the criteria of the statement that loaded the parent.
        return
    if _bypassed(state.session) or not _touches_tenant_model(state):
        return
    org_id = state.session.info.get(_TENANT_KEY)
    if org_id is None:
        raise TenantContextMissing("tenant-scoped query without an organization context")
    state.statement = state.statement.options(
        with_loader_criteria(TenantScoped, lambda cls: cls.organization_id == org_id, include_aliases=True)
    )


@event.listens_for(Session, "before_flush")
def _guard_tenant_writes(session: Session, _flush_context, _instances) -> None:
    if _bypassed(session):
        return
    org_id = session.info.get(_TENANT_KEY)
    for obj in session.new:
        if not isinstance(obj, TenantScoped):
            continue
        if org_id is None:
            if obj.organization_id is None:
                raise TenantContextMissing(f"cannot create {type(obj).__name__} without an organization context")
            continue
        if obj.organization_id is None:
            obj.organization_id = org_id
        elif obj.organization_id != org_id:
            raise TenantViolation(f"{type(obj).__name__} belongs to another organization")
    for obj in session.dirty:
        if not isinstance(obj, TenantScoped):
            continue
        if inspect(obj).attrs.organization_id.history.deleted:
            raise TenantViolation(f"{type(obj).__name__} cannot be moved to another organization")
    for obj in session.deleted:
        if isinstance(obj, TenantScoped) and org_id is not None and obj.organization_id != org_id:
            raise TenantViolation(f"{type(obj).__name__} belongs to another organization")


@event.listens_for(Session, "after_begin")
def _set_pg_tenant(session: Session, _transaction, connection) -> None:
    _push_pg_settings(session, connection)


def _push_pg_settings(session: Session, connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    for key, setting in _PG_SETTINGS.items():
        value = session.info.get(key)
        if value is not None:
            # is_local=true: the value lives only as long as the current transaction.
            connection.execute(text("SELECT set_config(:name, :value, true)"), {"name": setting, "value": value})


def org_unique_id():
    """Table arg making (organization_id, id) referenceable by same-organization composite foreign keys."""
    return UniqueConstraint("organization_id", "id")


def same_org_fk(column: str, target_table: str, ondelete: str | None = None):
    """Composite FK so the database itself refuses to link rows of different organizations.

    The default (NO ACTION) still lets deleting a whole organization cascade through its rows.
    Never use SET NULL here: it would also null organization_id.
    """
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target_table}.organization_id", f"{target_table}.id"],
        ondelete=ondelete,
    )
