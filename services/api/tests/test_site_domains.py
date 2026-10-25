"""Customers' own domains: proven with DNS, then the production site is served for that Host."""

from __future__ import annotations

import pytest

from novara.studio import domains
from tests.test_studio import DEV, OWNER, OUTSIDER, studio, url, version  # noqa: F401


@pytest.fixture
def dns(app):
    """A stand-in DNS: name -> TXT strings."""
    records: dict[str, list[str]] = {}
    app.state.domain_lookup = lambda name: records.get(name, [])
    domains._cache.clear()
    yield records
    domains._cache.clear()


def go_live(api, s):
    v = version(api, s, "launch")
    dep = api.req("POST", DEV, url(s, "/deployments"), json={"version": v["number"], "environment": "production"}).json()
    assert api.req("POST", OWNER, url(s, f"/deployments/{dep['id']}/approve")).json()["status"] == "live"


def test_names_are_checked_and_kept_in_one_form():
    assert domains.normalise(" WWW.Shop.Example.COM. ") == "www.shop.example.com"
    assert domains.normalise("متجر.السعودية") == "xn--pgbep1f.xn--mgberp4a5d4ar"     # Arabic domains as IDNA (as browsers send them)
    for bad in ("localhost", "10.0.0.1", "shop", "https://shop.com/x", "*.shop.com", "shop.local", "-a.com", "a" * 64 + ".com"):
        with pytest.raises(domains.DomainError):
            domains.normalise(bad)


def test_a_proven_domain_serves_the_production_site(api, studio, client, dns):
    add = lambda host, user=DEV: api.req("POST", user, url(studio, "/domains"), json={"hostname": host})  # noqa: E731
    assert add("shop.nokhba.sa").status_code == 403                      # adding a domain is a deploy decision
    assert add("localhost", OWNER).status_code == 422
    assert add("localhost:5173", OWNER).status_code == 422
    d = add("Shop.Nokhba.sa", OWNER)
    assert d.status_code == 201, d.text
    d = d.json()
    assert (d["hostname"], d["status"], d["txt_name"]) == ("shop.nokhba.sa", "pending", "_novara-challenge.shop.nokhba.sa")
    assert d["txt_value"].startswith("novara-") and d["cname_target"] == "localhost"
    assert add("shop.nokhba.sa", OWNER).status_code == 409               # already added here

    site = lambda path="/": client.get(path, headers={"host": "shop.nokhba.sa"})  # noqa: E731
    verify = lambda: api.req("POST", OWNER, url(studio, f"/domains/{d['id']}/verify"))  # noqa: E731
    # Not proven yet: the host is just another unknown host (the API answers as usual).
    assert client.get("/health", headers={"host": "shop.nokhba.sa"}).json() == {"status": "ok"}
    dns["_novara-challenge.shop.nokhba.sa"] = ["something else"]
    pending = verify().json()
    assert pending["status"] == "pending" and "expected value" in pending["last_error"]
    dns["_novara-challenge.shop.nokhba.sa"] = [d["txt_value"]]
    assert verify().json()["status"] == "verified"
    assert site().status_code == 404 and "isn't published" in site().text   # proven, nothing live yet
    assert client.get("/api/v1/public/domains/check", params={"domain": "shop.nokhba.sa"}).status_code == 404

    go_live(api, studio)
    domains._cache.clear()                                               # the cache would notice within a minute
    page = site()
    assert page.status_code == 200 and '<html lang="ar" dir="rtl">' in page.text
    assert page.headers["content-security-policy"].startswith("sandbox allow-scripts")
    assert site("/app.js").headers["content-type"].startswith(("application/javascript", "text/javascript"))
    assert site("/nope.html").status_code == 404
    assert site("/../../etc/passwd").status_code == 404
    assert client.get("/api/v1/public/domains/check", params={"domain": "SHOP.nokhba.sa"}).status_code == 200
    assert client.get("/api/v1/public/domains/check", params={"domain": "other.example.org"}).status_code == 404
    assert client.get("/health").json() == {"status": "ok"}             # the platform's own host is untouched

    # Another organization can claim it, but not prove it while it's taken.
    api.signup("rival@studio.example.com")
    rival_org = api.create_org("rival@studio.example.com", "rival-co")
    rival_project = api.req("POST", "rival@studio.example.com", f"/orgs/{rival_org}/studio/projects",
                            json={"name": "Rival", "slug": "rival"}).json()["id"]
    rival = api.req("POST", "rival@studio.example.com", f"/orgs/{rival_org}/studio/projects/{rival_project}/domains",
                    json={"hostname": "shop.nokhba.sa"})
    assert rival.status_code == 409 and "in use" in rival.json()["detail"]
    assert api.req("GET", "rival@studio.example.com",
                   f"/orgs/{rival_org}/studio/projects/{rival_project}/domains").json() == []
    assert api.req("POST", "rival@studio.example.com",
                   f"/orgs/{rival_org}/studio/projects/{rival_project}/domains/{d['id']}/verify").status_code == 404

    # Removing it stops serving it.
    assert api.req("DELETE", OWNER, url(studio, f"/domains/{d['id']}")).status_code == 204
    assert client.get("/health", headers={"host": "shop.nokhba.sa"}).json() == {"status": "ok"}
    logs = [e["action"] for e in api.req("GET", OWNER, f"/orgs/{studio['org']}/audit-logs").json()]
    assert {"app.domain_added", "app.domain_verified", "app.domain_removed"} <= set(logs)


def test_a_dns_failure_is_reported_not_raised(api, studio, app):
    def broken(name):
        raise domains.DomainError("the DNS lookup didn't answer (Timeout); try again in a few minutes")

    app.state.domain_lookup = broken
    d = api.req("POST", OWNER, url(studio, "/domains"), json={"hostname": "www.nokhba.sa"}).json()
    out = api.req("POST", OWNER, url(studio, f"/domains/{d['id']}/verify")).json()
    assert out["status"] == "pending" and "didn't answer" in out["last_error"] and out["last_checked_at"]
