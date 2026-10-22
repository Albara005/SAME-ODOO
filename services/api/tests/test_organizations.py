"""Phase 1 acceptance: create an organization, add users, and prove each
organization's data is only reachable by its own members with the right permissions."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from novara.modules.audit.models import AuditLog
from novara.modules.audit.service import AuditLogImmutable
from novara.tenancy import bind_tenant

OWNER, ADMIN, MEMBER, VIEWER, OUTSIDER = (f"{n}@example.com" for n in ("owner", "admin", "member", "viewer", "outsider"))


@pytest.fixture
def org(api):
    for email in (OWNER, ADMIN, MEMBER, VIEWER, OUTSIDER):
        api.signup(email)
    org_id = api.create_org(OWNER, "acme")
    for email, role in ((ADMIN, "admin"), (MEMBER, "member"), (VIEWER, "viewer")):
        assert api.add_member(OWNER, org_id, email, role).status_code == 201
    return org_id


def members(api, org_id, as_user=OWNER):
    return {m["email"]: m for m in api.req("GET", as_user, f"/orgs/{org_id}/members").json()}


def test_creator_becomes_owner(api, org):
    mine = api.req("GET", OWNER, "/orgs").json()
    assert [(o["slug"], o["role"]) for o in mine] == [("acme", "owner")]
    assert mine[0]["hosting_mode"] == "shared"


def test_slug_is_unique(api, org):
    r = api.req("POST", ADMIN, "/orgs", json={"name": "Other", "slug": "acme"})
    assert r.status_code == 409


def test_outsider_cannot_see_organization_at_all(api, org):
    for path in ("", "/members", "/roles", "/audit-logs", "/files"):
        assert api.req("GET", OUTSIDER, f"/orgs/{org}{path}").status_code == 404, path
    assert api.req("GET", OUTSIDER, "/orgs").json() == []


def test_two_organizations_never_see_each_others_data(api, org):
    other = api.create_org(OUTSIDER, "globex")
    api.req("POST", OUTSIDER, f"/orgs/{other}/files", params={"filename": "secret.txt"}, content=b"globex secret")
    api.req("POST", OWNER, f"/orgs/{org}/files", params={"filename": "plan.txt"}, content=b"acme plan")

    acme_files = api.req("GET", OWNER, f"/orgs/{org}/files").json()
    globex_files = api.req("GET", OUTSIDER, f"/orgs/{other}/files").json()
    assert [f["filename"] for f in acme_files] == ["plan.txt"]
    assert [f["filename"] for f in globex_files] == ["secret.txt"]

    # Using one's own org in the URL with the other org's file id still finds nothing.
    globex_file = globex_files[0]["id"]
    assert api.req("GET", OWNER, f"/orgs/{org}/files/{globex_file}/content").status_code == 404
    assert api.req("DELETE", OWNER, f"/orgs/{org}/files/{globex_file}").status_code == 404
    assert api.req("GET", OUTSIDER, f"/orgs/{other}/files/{globex_file}/content").content == b"globex secret"

    assert set(members(api, org)) == {OWNER, ADMIN, MEMBER, VIEWER}
    assert set(members(api, other, OUTSIDER)) == {OUTSIDER}


def test_member_ids_from_another_org_cannot_be_modified(api, org):
    other = api.create_org(OUTSIDER, "globex")
    outsider_membership = members(api, other, OUTSIDER)[OUTSIDER]["id"]
    r = api.req("DELETE", OWNER, f"/orgs/{org}/members/{outsider_membership}")
    assert r.status_code == 404
    assert OUTSIDER in members(api, other, OUTSIDER)


def test_permissions_are_enforced_on_the_server(api, org):
    assert api.req("GET", VIEWER, f"/orgs/{org}/members").status_code == 403
    assert api.req("GET", MEMBER, f"/orgs/{org}/members").status_code == 200
    assert api.add_member(MEMBER, org, OUTSIDER, "viewer").status_code == 403
    assert api.req("GET", MEMBER, f"/orgs/{org}/audit-logs").status_code == 403
    assert api.req("PATCH", ADMIN, f"/orgs/{org}", json={"name": "Renamed"}).status_code == 403
    assert api.req("PATCH", OWNER, f"/orgs/{org}", json={"name": "Renamed"}).status_code == 200
    r = api.req("POST", VIEWER, f"/orgs/{org}/files", params={"filename": "x"}, content=b"x")
    assert r.status_code == 403


def test_roles_do_not_carry_across_organizations(api, org):
    # MEMBER owns their own org, but is only a "member" in acme.
    own = api.create_org(MEMBER, "member-co")
    assert api.req("GET", MEMBER, f"/orgs/{own}/audit-logs").status_code == 200
    assert api.req("GET", MEMBER, f"/orgs/{org}/audit-logs").status_code == 403
    roles = {o["slug"]: o["role"] for o in api.req("GET", MEMBER, "/orgs").json()}
    assert roles == {"acme": "member", "member-co": "owner"}


def test_admin_cannot_escalate_to_owner_or_touch_owner(api, org):
    assert api.add_member(ADMIN, org, OUTSIDER, "owner").status_code == 403
    m = members(api, org)
    assert api.req("PATCH", ADMIN, f"/orgs/{org}/members/{m[ADMIN]['id']}", json={"role": "owner"}).status_code == 403
    assert api.req("DELETE", ADMIN, f"/orgs/{org}/members/{m[OWNER]['id']}").status_code == 403
    # but can manage less privileged members
    assert api.req("PATCH", ADMIN, f"/orgs/{org}/members/{m[VIEWER]['id']}", json={"role": "member"}).status_code == 200


def test_custom_role_cannot_exceed_creator_permissions(api, org):
    r = api.req("POST", ADMIN, f"/orgs/{org}/roles",
                json={"key": "auditor", "name": "Auditor", "permissions": ["audit.read"]})
    assert r.status_code == 201
    r = api.req("POST", ADMIN, f"/orgs/{org}/roles",
                json={"key": "boss", "name": "Boss", "permissions": ["org.manage"]})
    assert r.status_code == 403
    r = api.req("POST", OWNER, f"/orgs/{org}/roles",
                json={"key": "typo", "name": "Typo", "permissions": ["everything"]})
    assert r.status_code == 422


def test_system_roles_are_read_only(api, org):
    roles = {r["key"]: r for r in api.req("GET", OWNER, f"/orgs/{org}/roles").json()}
    r = api.req("PATCH", OWNER, f"/orgs/{org}/roles/{roles['viewer']['id']}", json={"permissions": ["audit.read"]})
    assert r.status_code == 409


def test_last_owner_cannot_leave_or_be_demoted(api, org):
    owner_m = members(api, org)[OWNER]["id"]
    assert api.req("DELETE", OWNER, f"/orgs/{org}/members/{owner_m}").status_code == 409
    assert api.req("PATCH", OWNER, f"/orgs/{org}/members/{owner_m}", json={"role": "admin"}).status_code == 409
    assert api.req("PATCH", OWNER, f"/orgs/{org}/members/{owner_m}", json={"status": "suspended"}).status_code == 409
    # With a second owner, the first can step down.
    admin_m = members(api, org)[ADMIN]["id"]
    assert api.req("PATCH", OWNER, f"/orgs/{org}/members/{admin_m}", json={"role": "owner"}).status_code == 200
    assert api.req("PATCH", OWNER, f"/orgs/{org}/members/{owner_m}", json={"role": "admin"}).status_code == 200


def test_suspended_member_loses_access(api, org):
    m = members(api, org)[MEMBER]["id"]
    assert api.req("PATCH", OWNER, f"/orgs/{org}/members/{m}", json={"status": "suspended"}).status_code == 200
    assert api.req("GET", MEMBER, f"/orgs/{org}/files").status_code == 404


def test_sensitive_operations_are_audited(api, org):
    m = members(api, org)[VIEWER]["id"]
    api.req("PATCH", OWNER, f"/orgs/{org}/members/{m}", json={"role": "member"})
    logs = api.req("GET", OWNER, f"/orgs/{org}/audit-logs").json()
    actions = [l["action"] for l in logs]
    assert actions.count("member.added") == 3
    assert "org.created" in actions
    changed = next(l for l in logs if l["action"] == "member.updated")
    assert changed["details"]["role"] == {"from": "viewer", "to": "member"}


def test_audit_log_is_append_only(api, app, org):
    with app.state.sessionmaker() as db:
        bind_tenant(db, org)
        entry = db.scalars(select(AuditLog)).first()
        entry.action = "tampered"
        with pytest.raises(AuditLogImmutable):
            db.flush()
        db.rollback()
        db.delete(db.scalars(select(AuditLog)).first())
        with pytest.raises(AuditLogImmutable):
            db.flush()
