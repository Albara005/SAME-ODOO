"""Phase 4 acceptance: apps of a defined scope are created, checked and deployed safely,
within the declared limits.

    template -> AI builder edits the working copy -> version (checks) -> preview
             -> production request -> approval by someone allowed to deploy -> live site
"""

from __future__ import annotations

import io
import zipfile

import pytest

from novara.ai.provider import ScriptedProvider, text_reply, tool_reply

OWNER, DEV, OUTSIDER = (f"{n}@studio.example.com" for n in ("owner", "dev", "outsider"))

ABOUT = """<!doctype html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>من نحن</title><link rel="stylesheet" href="styles.css"></head>
<body><h1>من نحن</h1><a href="index.html">الرئيسية</a></body>
</html>
"""


@pytest.fixture
def studio(api, app):
    for e in (OWNER, DEV, OUTSIDER):
        api.signup(e)
    org = api.create_org(OWNER, "studio-co")
    api.add_member(OWNER, org, DEV, "developer")
    provider = ScriptedProvider()
    app.state.ai_provider = provider
    r = api.req("POST", DEV, f"/orgs/{org}/studio/projects",
                json={"name": "متجر <النخبة>", "slug": "nokhba-shop", "description": "متجر إلكتروني"})
    assert r.status_code == 201, r.text
    return {"org": org, "project": r.json()["id"], "ai": provider}


def url(s, rest=""):
    return f"/orgs/{s['org']}/studio/projects/{s['project']}{rest}"


def put_file(api, s, path, text, user=DEV):
    return api.req("PUT", user, url(s, f"/files/{path}"), content=text.encode())


def get_file(api, s, path, **params):
    return api.req("GET", DEV, url(s, f"/files/{path}"), params=params)


def version(api, s, message="v", user=DEV):
    r = api.req("POST", user, url(s, "/versions"), json={"message": message})
    assert r.status_code == 201, r.text
    return r.json()


def test_template_starter_is_valid_and_escapes_user_input(api, studio):
    project = api.req("GET", DEV, url(studio)).json()
    assert {f["path"] for f in project["files"]} == {"index.html", "styles.css", "app.js"}
    index = get_file(api, studio, "index.html").text
    assert "متجر &lt;النخبة&gt;" in index and "<النخبة>" not in index  # name is HTML-escaped
    checks = api.req("POST", DEV, url(studio, "/checks")).json()
    assert {c["status"] for c in checks} == {"pass"}, checks


def test_ai_builder_edits_only_the_working_copy(api, studio):
    studio["ai"].script = [
        tool_reply("read_file", {"path": "index.html"}, tool_use_id="t1"),
        tool_reply("write_file", {"path": "about.html", "content": ABOUT}, tool_use_id="t2"),
        tool_reply("run_checks", {}, tool_use_id="t3"),
        text_reply("أضفت صفحة «من نحن» وكل الفحوصات ناجحة."),
    ]
    r = api.req("POST", DEV, url(studio, "/builder/messages"), json={"content": "أضف صفحة من نحن"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["changed_files"] == ["about.html"]
    assert [c["status"] for c in out["tool_calls"]] == ["executed", "executed", "executed"]
    assert "content" not in out["tool_calls"][1]["arguments"]           # large bodies aren't echoed back

    first = studio["ai"].requests[0]
    assert {t["name"] for t in first["tools"]} == {"list_files", "read_file", "write_file", "delete_file",
                                                   "run_checks"}       # nothing that versions or deploys
    assert (first["model"], first["effort"], first["stream"]) == ("claude-opus-5-5", "high", True)
    assert "static web app" in first["system"]
    read_back = studio["ai"].requests[1]["messages"][-1]["content"][0]["content"]
    assert "<!doctype html>" in read_back
    checks_result = studio["ai"].requests[3]["messages"][-1]["content"][0]["content"]
    assert "all checks pass" in checks_result

    assert get_file(api, studio, "about.html").text == ABOUT
    project = api.req("GET", DEV, url(studio)).json()
    assert project["has_unsaved_changes"] is True
    assert api.req("GET", DEV, url(studio, "/versions")).json() == []    # the user decides on versions
    assert api.req("GET", DEV, url(studio, "/deployments")).json() == []


@pytest.mark.parametrize("bad_path", ["../../etc/passwd", "/etc/passwd", ".env", "a/../../x.html", "dir\\x.js"])
def test_builder_cannot_escape_the_project(api, studio, bad_path):
    studio["ai"].script = [tool_reply("write_file", {"path": bad_path, "content": "x"}), text_reply("ok")]
    out = api.req("POST", DEV, url(studio, "/builder/messages"), json={"content": "write"}).json()
    assert out["tool_calls"][0]["status"] == "failed"
    assert out["changed_files"] == []


def test_versions_diff_restore_and_export(api, studio):
    v1 = version(api, studio, "starter")
    assert (v1["number"], v1["checks_passed"]) == (1, True)
    assert api.req("POST", DEV, url(studio, "/versions"), json={}).status_code == 409   # nothing changed

    put_file(api, studio, "app.js", 'console.log("v2");\n')
    d = api.req("GET", DEV, url(studio, "/diff")).json()
    assert [(f["path"], f["status"]) for f in d["files"]] == [("app.js", "modified")]
    assert '+console.log("v2");' in d["files"][0]["diff"]
    v2 = version(api, studio, "log")
    assert v2["number"] == 2

    api.req("POST", DEV, url(studio, "/versions/1/restore"))
    assert "addEventListener" in get_file(api, studio, "app.js").text
    assert "v2" in get_file(api, studio, "app.js", version=2).text      # history untouched

    z = zipfile.ZipFile(io.BytesIO(api.req("GET", DEV, url(studio, "/versions/2/export.zip")).content))
    assert sorted(z.namelist()) == ["nokhba-shop/app.js", "nokhba-shop/index.html", "nokhba-shop/styles.css"]


def test_failing_checks_block_production_but_not_preview(api, studio):
    put_file(api, studio, "config.js", 'const key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123";\n')
    put_file(api, studio, "index.html", get_file(api, studio, "index.html").text.replace("app.js", "missing.js"))
    v = version(api, studio, "broken")
    assert v["checks_passed"] is False
    failed = {c["check"] for c in v["checks"] if c["status"] == "fail"}
    assert failed == {"secrets", "local_references"}
    prod = api.req("POST", DEV, url(studio, "/deployments"), json={"version": 1, "environment": "production"})
    assert prod.status_code == 409
    preview = api.req("POST", DEV, url(studio, "/deployments"), json={"version": 1, "environment": "preview"})
    assert preview.status_code == 201 and preview.json()["status"] == "live"


def test_production_needs_separate_approval_and_is_served_sandboxed(api, studio, client):
    v1 = version(api, studio, "launch")
    req = api.req("POST", DEV, url(studio, "/deployments"), json={"version": v1["number"], "environment": "production"})
    assert req.status_code == 201
    dep = req.json()
    assert (dep["status"], dep["url"]) == ("pending_approval", None)
    # The developer can request but not approve production.
    assert api.req("POST", DEV, url(studio, f"/deployments/{dep['id']}/approve")).status_code == 403
    live = api.req("POST", OWNER, url(studio, f"/deployments/{dep['id']}/approve")).json()
    assert live["status"] == "live"
    site = live["url"].split("/sites/", 1)[1]

    page = client.get(f"/sites/{site}")
    assert page.status_code == 200
    assert "<html lang=\"ar\" dir=\"rtl\">" in page.text
    assert page.headers["content-security-policy"].startswith("sandbox allow-scripts")
    assert page.headers["content-type"].startswith("text/html")
    assert client.get(f"/sites/{site}app.js").headers["content-type"].startswith(("application/javascript", "text/javascript"))
    assert client.get(f"/sites/{site}nope.html").status_code == 404
    assert client.get("/sites/s-not-a-real-key/").status_code == 404

    # New version: same production URL now serves v2; v1's deployment is superseded.
    put_file(api, studio, "app.js", 'console.log("v2");\n')
    v2 = version(api, studio, "v2")
    d2 = api.req("POST", DEV, url(studio, "/deployments"), json={"version": v2["number"], "environment": "production"}).json()
    again = api.req("POST", DEV, url(studio, "/deployments"), json={"version": v2["number"], "environment": "production"})
    assert again.status_code == 409                                     # one pending request at a time
    api.req("POST", OWNER, url(studio, f"/deployments/{d2['id']}/approve"))
    assert "v2" in client.get(f"/sites/{site}app.js").text
    statuses = {d["version"]: d["status"] for d in api.req("GET", DEV, url(studio, "/deployments")).json()}
    assert statuses == {1: "superseded", 2: "live"}

    # Rollback is a deployment of the older version, through the same approval.
    d3 = api.req("POST", DEV, url(studio, "/deployments"), json={"version": 1, "environment": "production"}).json()
    api.req("POST", OWNER, url(studio, f"/deployments/{d3['id']}/approve"))
    assert "addEventListener" in client.get(f"/sites/{site}app.js").text

    # Kill switch: everything goes offline at once.
    api.req("POST", OWNER, url(studio, "/offline"))
    assert client.get(f"/sites/{site}").status_code == 404

    logs = [l["action"] for l in api.req("GET", OWNER, f"/orgs/{studio['org']}/audit-logs").json()]
    assert logs.count("app.production_deployed") == 3 and "app.taken_offline" in logs


def test_rejected_production_request(api, studio):
    v = version(api, studio)
    d = api.req("POST", DEV, url(studio, "/deployments"), json={"version": v["number"], "environment": "production"}).json()
    r = api.req("POST", OWNER, url(studio, f"/deployments/{d['id']}/reject"))
    assert r.json()["status"] == "rejected"
    assert api.req("POST", OWNER, url(studio, f"/deployments/{d['id']}/approve")).status_code == 409


def test_limits(api, studio):
    assert put_file(api, studio, "big.txt", "x" * (512 * 1024 + 1)).status_code == 413
    assert put_file(api, studio, "tool.exe", "MZ").status_code == 200        # stored, but the check fails it
    v = version(api, studio)
    assert not v["checks_passed"]
    assert any(c["check"] == "file_types" and c["status"] == "fail" for c in v["checks"])
    assert put_file(api, studio, "../escape.html", "x").status_code in (404, 422)


def test_projects_are_isolated_and_permissioned(api, studio):
    other = api.create_org(OUTSIDER, "other-studio")
    assert api.req("GET", OUTSIDER, f"/orgs/{other}/studio/projects/{studio['project']}").status_code == 404
    assert api.req("GET", OUTSIDER, f"/orgs/{other}/studio/projects").json() == []
    api.add_member(OWNER, studio["org"], OUTSIDER, "viewer")
    assert api.req("GET", OUTSIDER, url(studio)).status_code == 403
    dup = api.req("POST", DEV, f"/orgs/{studio['org']}/studio/projects", json={"name": "x", "slug": "nokhba-shop"})
    assert dup.status_code == 409
