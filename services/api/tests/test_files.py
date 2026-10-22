from __future__ import annotations

from tests.conftest import Api


def _setup(api: Api) -> str:
    api.signup("o@example.com")
    return api.create_org("o@example.com", "files-co")


def test_upload_list_download_delete(api, app):
    org = _setup(api)
    body = "تقرير المبيعات".encode()
    r = api.req("POST", "o@example.com", f"/orgs/{org}/files", params={"filename": "تقرير.txt"},
                content=body, headers={"Content-Type": "text/plain; charset=utf-8"})
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["size_bytes"] == len(body)
    assert len(f["sha256"]) == 64

    got = api.req("GET", "o@example.com", f"/orgs/{org}/files/{f['id']}/content")
    assert got.content == body
    assert got.headers["x-content-type-options"] == "nosniff"

    stored = list((app.state.storage.root / "orgs" / org / "files").iterdir())
    assert [p.name for p in stored] == [f["id"]]

    assert api.req("DELETE", "o@example.com", f"/orgs/{org}/files/{f['id']}").status_code == 204
    assert api.req("GET", "o@example.com", f"/orgs/{org}/files").json() == []
    assert not (app.state.storage.root / "orgs" / org / "files" / f["id"]).exists()


def test_upload_size_limit(api):
    org = _setup(api)
    r = api.req("POST", "o@example.com", f"/orgs/{org}/files", params={"filename": "big"}, content=b"x" * 2048)
    assert r.status_code == 413
    assert api.req("GET", "o@example.com", f"/orgs/{org}/files").json() == []
