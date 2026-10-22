"""The data-access layer itself enforces organization isolation."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from novara.modules.access.models import Role
from novara.modules.organizations.models import Organization
from novara.tenancy import TenantContextMissing, TenantViolation, bind_tenant


@pytest.fixture
def orgs(app):
    """Two organizations, each with one role. Returns (sessionmaker, org_a, org_b, role_ids)."""
    make = app.state.sessionmaker
    with make() as db:
        a, b = Organization(name="A", slug="org-a"), Organization(name="B", slug="org-b")
        db.add_all([a, b])
        db.commit()
    role_ids = {}
    for org in (a, b):
        with make() as db:
            bind_tenant(db, org.id)
            role = Role(key="custom", name=f"role of {org.slug}", permissions=[])
            db.add(role)
            db.commit()
            role_ids[org.id] = role.id
    return make, a.id, b.id, role_ids


def _role_names(make, org_id):
    with make() as db:
        bind_tenant(db, org_id)
        return [r.name for r in db.scalars(select(Role))]


def test_query_without_tenant_context_fails_loudly(orgs):
    make, *_ = orgs
    with make() as db, pytest.raises(TenantContextMissing):
        db.scalars(select(Role)).all()


def test_queries_are_filtered_to_bound_organization(orgs):
    make, a, b, _ = orgs
    with make() as db:
        bind_tenant(db, a)
        roles = db.scalars(select(Role)).all()
        assert {r.organization_id for r in roles} == {a}


def test_get_by_primary_key_cannot_reach_other_organization(orgs):
    make, a, b, role_ids = orgs
    with make() as db:
        bind_tenant(db, a)
        assert db.get(Role, role_ids[b]) is None
        assert db.get(Role, role_ids[a]) is not None


def test_bulk_update_cannot_touch_other_organization(orgs):
    make, a, b, _ = orgs
    with make() as db:
        bind_tenant(db, a)
        db.execute(update(Role).values(name="hijacked"))
        db.commit()
    assert _role_names(make, a) == ["hijacked"]
    assert _role_names(make, b) == ["role of org-b"]


def test_insert_is_stamped_with_bound_organization(orgs):
    make, a, *_ = orgs
    with make() as db:
        bind_tenant(db, a)
        role = Role(key="stamped", name="x", permissions=[])
        db.add(role)
        db.commit()
        assert role.organization_id == a


def test_insert_into_other_organization_is_rejected(orgs):
    make, a, b, _ = orgs
    with make() as db:
        bind_tenant(db, a)
        db.add(Role(organization_id=b, key="sneaky", name="x", permissions=[]))
        with pytest.raises(TenantViolation):
            db.flush()


def test_moving_row_to_other_organization_is_rejected(orgs):
    make, a, b, _ = orgs
    with make() as db:
        bind_tenant(db, a)
        role = db.scalars(select(Role)).first()
        role.organization_id = b
        with pytest.raises(TenantViolation):
            db.flush()


def test_session_cannot_be_rebound_to_another_organization(orgs):
    make, a, b, _ = orgs
    with make() as db:
        bind_tenant(db, a)
        with pytest.raises(TenantViolation):
            bind_tenant(db, b)
